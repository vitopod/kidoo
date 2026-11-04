"""Optional destructive-to-test-progress integration test on disposable Jellyfin.

Set KIDOO_TEST_DISPOSABLE_JELLYFIN=1, KIDOO_TEST_JELLYFIN_CONFIG to a JSON
connection and KIDOO_TEST_JELLYFIN_ITEM to a rights-cleared film ID. Only a
loopback server is accepted. The chosen item's Jellyfin watch position changes.
"""
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
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import jellyfin
from kidoo_server import KidooServer

class RealJellyfinTest(unittest.TestCase):
    def setUp(self):
        path=os.environ.get('KIDOO_TEST_JELLYFIN_CONFIG','')
        self.item=os.environ.get('KIDOO_TEST_JELLYFIN_ITEM','')
        if os.environ.get('KIDOO_TEST_DISPOSABLE_JELLYFIN')!='1' or not path or not self.item or not shutil.which('ffmpeg'):
            self.skipTest('Explicit disposable Jellyfin configuration, test item and FFmpeg required')
        self.mapping=json.loads(Path(path).read_text())
        if urlsplit(self.mapping['server']).hostname not in ('127.0.0.1','localhost','::1'):
            raise ValueError('Disposable Jellyfin must be bound to loopback')
        if not self.item.isalnum():raise ValueError('Invalid test item ID')
        self.tmp=tempfile.TemporaryDirectory(prefix='kidoo-jellyfin-integration-');self.root=Path(self.tmp.name)
        self.media=self.root/'media';self.media.mkdir();jellyfin.save_config(self.root,'family',self.mapping)
        self.start()
    def start(self):
        self.server=KidooServer(('127.0.0.1',0),self.media,self.root/'kidoo.db','')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def stop(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def tearDown(self):self.stop();self.tmp.cleanup()
    def request(self,method,path,data=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=15);headers={}
        if data is not None:headers['Content-Type']='application/json';data=json.dumps(data)
        conn.request(method,path,data,headers);response=conn.getresponse();raw=response.read();status=response.status;conn.close()
        self.assertLess(status,400,f'{path}: HTTP {status}')
        return json.loads(raw)
    def test_real_jellyfin_playback_and_resume(self):
        self.request('POST','/api/jellyfin/approval',{'profileId':'family','itemId':self.item,'approved':True})
        home=self.request('GET','/api/tv/home/family')
        self.assertTrue(any(v['id']==self.item for v in home['jellyfin']))
        session=self.request('POST','/api/jellyfin/start',{'profileId':'family','itemId':self.item})
        url=f'http://127.0.0.1:{self.server.server_port}'+session['streamUrl']
        decode=subprocess.run(['ffmpeg','-v','error','-ss','60','-i',url,'-t','2','-f','null','-'],capture_output=True,timeout=30)
        self.assertEqual(decode.returncode,0,decode.stderr.decode(errors='replace')[-500:])
        self.request('POST','/api/jellyfin/event',{'profileId':'family','sessionId':session['sessionId'],'sequence':1,'event':'stop','position':60,'paused':True})
        self.stop();self.start()
        resume=self.request('POST','/api/jellyfin/start',{'profileId':'family','itemId':self.item})
        self.assertAlmostEqual(resume['position'],60,delta=1)
        url=f'http://127.0.0.1:{self.server.server_port}'+resume['streamUrl']
        decode=subprocess.run(['ffmpeg','-v','error','-ss',str(resume['position']),'-i',url,'-t','2','-f','null','-'],capture_output=True,timeout=30)
        self.assertEqual(decode.returncode,0,decode.stderr.decode(errors='replace')[-500:])

if __name__=='__main__':unittest.main()
