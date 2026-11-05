"""A protected first-run and recovery rehearsal using an explicitly supplied real video.

Set KIDOO_TEST_MEDIA to a rights-cleared local video; the test never plays it.
"""
import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import backup
import connection_check
import security_admin
import setup_wizard
from security import SecurityError, SecurityStore

MEDIA=os.environ.get('KIDOO_TEST_MEDIA','')

@unittest.skipUnless(MEDIA and Path(MEDIA).is_file(),'Set KIDOO_TEST_MEDIA to a rights-cleared local video')
class FirstRunJourneyTest(unittest.TestCase):
    def test_clean_setup_approval_pairing_backup_and_recovery(self):
        with tempfile.TemporaryDirectory(prefix='kidoo-first-run-') as directory:
            root=Path(directory);media=root/'videos';media.mkdir();shutil.copy2(MEDIA,media/'sample.mp4')
            cert=root/'server.crt';key=root/'server.key';data=root/'data'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost','-keyout',str(key),'-out',str(cert)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            with socket.socket() as reservation:reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
            answers=['2',str(media),'1',str(cert),str(key),'','Parent','Example child','n','n','n','n']
            with patch.object(sys,'argv',['setup_wizard.py','--data-dir',str(data),'--port',str(port)]),patch('builtins.input',side_effect=answers),patch('getpass.getpass',side_effect=['482951','482951']),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(setup_wizard.main(),0)
            launcher=data/'start-kidoo.sh';process=subprocess.Popen([str(launcher)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            trust=ssl.create_default_context(cafile=str(cert))
            def request(method,path,body=None,cookie='',credential=''):
                connection=http.client.HTTPSConnection('localhost',port,context=trust,timeout=5);headers={}
                if body is not None:headers['Content-Type']='application/json'
                if cookie:headers['Cookie']=cookie
                if credential:headers['Authorization']='Bearer '+credential
                connection.request(method,path,None if body is None else json.dumps(body),headers);response=connection.getresponse()
                status=response.status;cookies=response.headers.get_all('Set-Cookie') or [];payload=json.loads(response.read());connection.close()
                return status,payload,cookies
            try:
                for attempt in range(50):
                    try:
                        if connection_check.check(f'https://localhost:{port}','127.0.0.1',context=trust)['ok']:break
                    except connection_check.ConnectionCheckError:time.sleep(.1)
                else:self.fail('Protected server did not become ready')
                status,_,cookies=request('POST','/api/security/admin-login',{'name':'Parent','pin':'482951'});self.assertEqual(status,200)
                parent=next(c for c in cookies if c.startswith('kidoo_session=')).split(';',1)[0]
                status,code,_=request('POST','/api/security/pairing',{'profileId':'family','label':'Test device'},cookie=parent);self.assertEqual(status,200)
                status,device,_=request('POST','/api/security/redeem',{'code':code['code']});self.assertEqual(status,200)
                credential=device['credential'];status,home,_=request('GET','/api/tv/home/family',credential=credential);self.assertEqual(status,200);self.assertEqual(home['local'],[])
                status,catalogue,_=request('GET','/api/local/catalogue?start=0&limit=24',cookie=parent);self.assertEqual(status,200)
                item=catalogue['items'][0]['id']
                status,_,_=request('POST','/api/security/approval',{'profileId':'family','kind':'local','itemId':item,'approved':True},cookie=parent);self.assertEqual(status,200)
                status,home,_=request('GET','/api/tv/home/family',credential=credential);self.assertEqual(status,200);self.assertEqual([v['id'] for v in home['local']],[item])
                snapshot=backup.create(data,root/'snapshot');restored=backup.restore(snapshot,root/'restored')
                recovered=SecurityStore(restored/'kidoo.db');self.assertTrue(recovered.is_approved('family','local',item));self.assertIsNotNone(recovered.device(credential))
                # Recovery is rehearsed only on the restored copy.
                with patch.object(sys,'argv',['security_admin.py','--data-dir',str(restored),'set-pin']),patch('getpass.getpass',side_effect=['592062','592062']),contextlib.redirect_stdout(io.StringIO()):self.assertEqual(security_admin.main(),0)
                with self.assertRaises(SecurityError):recovered.unlock(credential,'482951')
                self.assertTrue(recovered.unlock(credential,'592062'))
                # Start the restored database separately; the original remains live.
                with socket.socket() as reservation:reservation.bind(('127.0.0.1',0));restored_port=reservation.getsockname()[1]
                restored_process=subprocess.Popen([sys.executable,str(Path(setup_wizard.__file__).parent/'kidoo_server.py'),'--media-dir',str(media),'--data-dir',str(restored),'--host','127.0.0.1','--port',str(restored_port),'--protected-mode','--tls-cert',str(cert),'--tls-key',str(key)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                try:
                    for attempt in range(50):
                        try:
                            if connection_check.check(f'https://localhost:{restored_port}','127.0.0.1',context=trust)['ok']:break
                        except connection_check.ConnectionCheckError:time.sleep(.1)
                    else:self.fail('Restored server did not become ready')
                    restored_connection=http.client.HTTPSConnection('localhost',restored_port,context=trust,timeout=5)
                    restored_connection.request('GET','/api/tv/home/family',headers={'Authorization':'Bearer '+credential})
                    response=restored_connection.getresponse();self.assertEqual(response.status,200)
                    restored_home=json.loads(response.read());restored_connection.close()
                    self.assertEqual([v['id'] for v in restored_home['local']],[item])
                    restored_connection=http.client.HTTPSConnection('localhost',restored_port,context=trust,timeout=5)
                    restored_connection.request('POST','/api/security/admin-login',json.dumps({'name':'Parent','pin':'482951'}),{'Content-Type':'application/json'})
                    response=restored_connection.getresponse();self.assertEqual(response.status,403);response.read();restored_connection.close()
                    restored_connection=http.client.HTTPSConnection('localhost',restored_port,context=trust,timeout=5)
                    restored_connection.request('POST','/api/security/admin-login',json.dumps({'name':'Parent','pin':'592062'}),{'Content-Type':'application/json'})
                    response=restored_connection.getresponse();self.assertEqual(response.status,200);response.read();restored_connection.close()
                finally:restored_process.terminate();restored_process.wait(timeout=10)
                status,_,_=request('POST','/api/security/approval',{'profileId':'family','kind':'local','itemId':item,'approved':False},cookie=parent);self.assertEqual(status,200)
                status,_,_=request('GET','/media/'+item,credential=credential);self.assertEqual(status,403)
                status,_,_=request('POST','/api/security/revoke',{'deviceId':device['deviceId']},cookie=parent);self.assertEqual(status,200)
                self.assertEqual(request('GET','/api/tv/home/family',credential=credential)[0],401)
            finally:
                process.terminate();process.wait(timeout=10)

if __name__=='__main__':unittest.main()
