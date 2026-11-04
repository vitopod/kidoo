import contextlib
import http.client
import json
import io
import re
from pathlib import Path
import sqlite3
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'server'))
import setup_wizard


class SetupWizardTest(unittest.TestCase):
    def test_new_setup_creates_protected_credentials_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);media=root/'videos';media.mkdir()
            cert=root/'cert.pem';key=root/'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                            '-subj','/CN=localhost','-keyout',str(key),'-out',str(cert)],
                           check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
            data=root/'data';out=io.StringIO()
            answers=[str(media),str(cert),str(key),'Parent','n','y','Family TV','n']
            with patch.object(sys,'argv',['setup_wizard.py','--data-dir',str(data),'--port',str(port)]), patch('builtins.input',side_effect=answers), patch('getpass.getpass',side_effect=['482951','482951']), contextlib.redirect_stdout(out):
                self.assertEqual(setup_wizard.main(),0)
            launcher=data/'start-kidoo.sh'
            self.assertTrue(launcher.is_file())
            self.assertEqual(launcher.stat().st_mode & 0o777,0o700)
            self.assertIn('--protected-mode',launcher.read_text())
            pairing=re.search(r'Pairing code: ([A-Za-z0-9_-]+)',out.getvalue())
            self.assertIsNotNone(pairing)
            process=subprocess.Popen([str(launcher)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                def request(method,path,payload=None,credential=None):
                    connection=http.client.HTTPSConnection('127.0.0.1',port,context=ssl._create_unverified_context(),timeout=2)
                    headers={};body=None
                    if payload is not None:headers['Content-Type']='application/json';body=json.dumps(payload)
                    if credential:headers['Authorization']='Bearer '+credential
                    connection.request(method,path,body,headers)
                    response=connection.getresponse();result=(response.status,json.loads(response.read()));connection.close();return result
                for _ in range(50):
                    try:
                        if request('GET','/api/tv/info')[0]==200:break
                    except (OSError,ConnectionError):time.sleep(.1)
                else:self.fail('Generated launcher did not start the protected server')
                status,paired=request('POST','/api/security/redeem',{'code':pairing.group(1)})
                self.assertEqual(status,200);self.assertEqual(paired['profileId'],'family')
                self.assertEqual(request('GET','/api/profiles',credential=paired['credential'])[0],200)
                self.assertEqual(request('POST','/api/security/redeem',{'code':pairing.group(1)})[0],403)
            finally:
                process.terminate();process.wait(timeout=10)
            self.assertNotIn('482951',out.getvalue())
            self.assertEqual((data/'kidoo.db').stat().st_mode & 0o777,0o600)
            with sqlite3.connect(data/'kidoo.db') as db:
                self.assertEqual(db.execute('SELECT name FROM security_administrators').fetchone()[0],'Parent')
                self.assertTrue(db.execute('SELECT verifier FROM security_pin').fetchone())
                self.assertEqual(db.execute('SELECT COUNT(*) FROM security_devices').fetchone()[0],1)
            with patch.object(sys,'argv',['setup_wizard.py','--data-dir',str(data)]), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                setup_wizard.main()
