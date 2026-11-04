"""Real TLS and rights-cleared local media test for a paired child browser."""
import http.client
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from kidoo_server import KidooServer, media_index


@unittest.skipUnless(os.environ.get('KIDOO_TEST_MEDIA') and Path(os.environ['KIDOO_TEST_MEDIA']).is_file(),'Rights-cleared KIDOO_TEST_MEDIA required')
class BrowserHttpsTest(unittest.TestCase):
    def test_pair_stream_revoke_and_resume_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cert=root/'cert.pem';key=root/'key.pem';database=root/'kidoo.db'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost',
                            '-addext','subjectAltName=DNS:localhost','-keyout',str(key),'-out',str(cert)],
                           check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            media=Path(os.environ['KIDOO_TEST_MEDIA']).resolve(); index=media_index(media.parent)
            item=next(key for key,value in index.items() if value['path'].resolve()==media)
            client_context=ssl.create_default_context(cafile=str(cert))
            def create_server():
                server=KidooServer(('127.0.0.1',0),media.parent,database,'test');server.protected_mode=True
                context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key);server.socket=context.wrap_socket(server.socket,server_side=True)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();return server,thread
            server,thread=create_server();self.addCleanup(lambda: None)
            def request(method,path,data=None,cookie='',extra=None):
                conn=http.client.HTTPSConnection('localhost',server.server_port,context=client_context,timeout=5);headers={'X-Kidoo-Browser':'1'}
                if data is not None:headers['Content-Type']='application/json'
                if cookie:headers['Cookie']=cookie
                if extra:headers.update(extra)
                conn.request(method,path,None if data is None else json.dumps(data),headers);response=conn.getresponse()
                status=response.status;cookies=response.headers.get_all('Set-Cookie') or [];body=response.read();conn.close()
                return status,body,cookies
            try:
                server.security.create_administrator('Parent');server.security.set_pin('482951');server.security.set_approval('family','local',item,True)
                code=server.security.create_pairing('family','Browser')
                status,body,cookies=request('POST','/api/browser/pair',{'code':code});self.assertEqual(status,200)
                cookie=next(c for c in cookies if c.startswith('__Host-kidoo_child=')).split(';',1)[0]
                status,body,_=request('POST','/api/activity/start',{'profileId':'family','kind':'local','itemId':item},cookie)
                self.assertEqual(status,200);activity=json.loads(body)['sessionId']
                path='/media/'+item+'?profileId=family&activity='+activity
                status,body,_=request('GET',path,cookie=cookie,extra={'Range':'bytes=0-127'});self.assertEqual(status,206)
                with media.open('rb') as source:self.assertEqual(body,source.read(128))
                progress={'profileId':'family','mediaId':item,'position':24.0,'duration':300.0,'completed':False}
                self.assertEqual(request('POST','/api/progress',progress,cookie)[0],200)
                server.security.set_approval('family','local',item,False)
                self.assertEqual(request('GET',path,cookie=cookie)[0],403)
                server.security.set_approval('family','local',item,True)
                server.shutdown();server.server_close();thread.join();server,thread=create_server()
                with patch('kidoo_server.jellyfin.load_config',return_value={}):
                    status,body,_=request('GET','/api/tv/home/family',cookie=cookie)
                self.assertEqual(status,200);home=json.loads(body)
                self.assertEqual(next(v for v in home['local'] if v['id']==item)['progress']['position'],24.0)
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
