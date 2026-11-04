"""Optional real-media smoke test. Set KIDOO_TEST_MEDIA to a rights-cleared video.

The test never creates media files or modifies the source file. It starts an
isolated loopback server with temporary data, decodes the real stream and checks
resume across a server restart. Requires ffmpeg.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from kidoo_server import KidooServer

class RealPlaybackTest(unittest.TestCase):
    def setUp(self):
        path=os.environ.get('KIDOO_TEST_MEDIA','')
        if not path or not shutil.which('ffmpeg'):self.skipTest('Set KIDOO_TEST_MEDIA to a rights-cleared video and install ffmpeg')
        self.video=Path(path).resolve(strict=True)
        if not self.video.is_file() or self.video.suffix.lower() not in ('.mp4','.m4v','.webm','.mkv','.mov'):self.skipTest('A supported video file is required')
        self.temp=tempfile.TemporaryDirectory();self.database=Path(self.temp.name)/'database.db'
        self.start()
    def start(self):
        self.server=KidooServer(('127.0.0.1',0),self.video.parent,self.database,'')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def stop(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def tearDown(self):self.stop();self.temp.cleanup()
    def request(self,method,path,data=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=15);headers=dict(headers or {})
        if data is not None:headers['Content-Type']='application/json';data=json.dumps(data)
        connection.request(method,path,data,headers);response=connection.getresponse();result=(response.status,dict(response.getheaders()),response.read());connection.close();return result
    def test_real_stream_decodes_and_resume_survives_restart(self):
        ident=hashlib.sha256(self.video.name.encode()).hexdigest()[:24]
        self.assertEqual(self.request('POST','/api/approval',{'mediaId':ident,'approved':True})[0],200)
        status,headers,chunk=self.request('GET','/media/'+ident,headers={'Range':'bytes=0-1023'})
        self.assertEqual(status,206)
        with self.video.open('rb') as source:self.assertEqual(chunk,source.read(1024))
        base=f'http://127.0.0.1:{self.server.server_port}'
        decoded=subprocess.run(['ffmpeg','-v','error','-ss','60','-i',base+'/media/'+ident,'-t','2','-f','null','-'],capture_output=True,timeout=30)
        self.assertEqual(decoded.returncode,0,decoded.stderr.decode(errors='replace'))
        self.assertEqual(self.request('POST','/api/progress',{'profileId':'family','mediaId':ident,'position':60,'duration':120})[0],200)
        self.stop();self.start()
        library=json.loads(self.request('GET','/api/library/family')[2])
        saved=next(v for v in library if v['id']==ident)
        self.assertEqual(saved['progress']['position'],60)
        self.assertFalse(saved['progress']['completed'])
        decoded=subprocess.run(['ffmpeg','-v','error','-ss',str(saved['progress']['position']),'-i',f'http://127.0.0.1:{self.server.server_port}/media/{ident}','-t','2','-f','null','-'],capture_output=True,timeout=30)
        self.assertEqual(decoded.returncode,0,decoded.stderr.decode(errors='replace'))

if __name__=='__main__':unittest.main()
