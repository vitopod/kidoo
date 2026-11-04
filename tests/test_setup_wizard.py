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
import autostart
import certificate_health


class SetupWizardTest(unittest.TestCase):
    def test_new_setup_creates_protected_credentials_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);media=root/'videos';media.mkdir()
            cert=root/'cert.pem';key=root/'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                            '-subj','/CN=localhost','-keyout',str(key),'-out',str(cert)],
                           check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            health=certificate_health.read(cert)
            self.assertTrue(health['configured']);self.assertFalse(health['expired'])
            self.assertTrue(certificate_health.read(cert,now=health['expiresAt']+1)['expired'])
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
            data=root/'data';out=io.StringIO()
            answers=['2',str(media),'1',str(cert),str(key),'','Parent','n','y','Family TV','n','n']
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
            self.assertIn(f'https://localhost:{port}/dashboard',out.getvalue())
            self.assertEqual((data/'kidoo.db').stat().st_mode & 0o777,0o600)
            with sqlite3.connect(data/'kidoo.db') as db:
                self.assertEqual(db.execute('SELECT name FROM security_administrators').fetchone()[0],'Parent')
                self.assertTrue(db.execute('SELECT verifier FROM security_pin').fetchone())
                self.assertEqual(db.execute('SELECT COUNT(*) FROM security_devices').fetchone()[0],1)
            with patch.object(sys,'argv',['setup_wizard.py','--data-dir',str(data)]), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                with patch('builtins.input',return_value='2'): setup_wizard.main()

    def test_preview_uses_temporary_loopback_server_and_preserves_existing_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);media=root/'videos';media.mkdir();data=root/'existing';data.mkdir()
            (data/'kidoo.db').write_text('existing data')
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
            captured={}
            def run(command,env):
                captured['command']=command;captured['env']=env
                preview_data=Path(command[command.index('--data-dir')+1])
                self.assertTrue(preview_data.is_dir());self.assertNotEqual(preview_data,data)
                return 0
            with patch.object(sys,'argv',['setup_wizard.py','--data-dir',str(data),'--port',str(port)]), \
                 patch('builtins.input',side_effect=['1',str(media)]), \
                 patch.dict('os.environ',{'KIDOO_PASSWORD':'example-secret'}), \
                 patch('setup_wizard.subprocess.call',side_effect=run), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(setup_wizard.main(),0)
            command=captured['command']
            self.assertEqual(command[command.index('--host')+1],'127.0.0.1')
            self.assertNotIn('KIDOO_PASSWORD',captured['env'])
            self.assertFalse(Path(command[command.index('--data-dir')+1]).exists())
            self.assertEqual((data/'kidoo.db').read_text(),'existing data')

    def test_rejects_hostname_not_covered_by_certificate(self):
        info={'subjectAltName':(('DNS','tv.example.test'),)}
        with patch('setup_wizard.certificate_info',return_value=info), \
             patch('builtins.input',side_effect=['wrong.example.test','']), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(setup_wizard.ask_hostname(Path('certificate.pem')),'tv.example.test')
        self.assertIn('does not match',output.getvalue())

    def test_checks_expired_certificate_and_busy_port_before_initializing(self):
        self.assertIn('expired',setup_wizard.certificate_problem({'notAfter':'Jan  1 00:00:00 2020 GMT'},now=1600000000))
        with tempfile.TemporaryDirectory() as tmp, socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen(1)
            data=Path(tmp)/'new-data'
            with patch.object(sys,'argv',['setup_wizard.py','--data-dir',str(data),'--port',str(listener.getsockname()[1])]), \
                 patch('builtins.input',side_effect=['2']), contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit):
                setup_wizard.main()
            self.assertIn('unavailable',errors.getvalue())
            self.assertFalse(data.exists())

    def test_unsupported_protected_platform_stops_before_creating_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)/'new-data'
            with patch.object(sys,'argv',['setup_wizard.py','--data-dir',str(data)]), \
                 patch('builtins.input',return_value='2'), patch('setup_wizard.protected_setup_supported',return_value=False), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit):
                setup_wizard.main()
            self.assertIn('not yet supported on Windows',errors.getvalue())
            self.assertFalse(data.exists())

    def test_opt_in_autostart_preserves_existing_service_and_escapes_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);launcher=home/'family % videos'/'start-kidoo.sh'
            launcher.parent.mkdir();launcher.write_text('#!/bin/sh\n')
            calls=[]
            target=autostart.install(launcher,platform='linux',home=home,run=lambda cmd,check:calls.append(cmd))
            self.assertIn('family %% videos',target.read_text())
            self.assertEqual(autostart.systemd_quote('/path/$NAME'), '"/path/$$NAME"')
            self.assertEqual(calls[-1],['systemctl','--user','enable','--now','kidoo.service'])
            before=target.read_bytes()
            with self.assertRaises(FileExistsError): autostart.install(launcher,platform='linux',home=home,run=lambda *a,**k:None)
            self.assertEqual(target.read_bytes(),before)
            target_mac=autostart.install(launcher,platform='darwin',home=home,run=lambda cmd,check:calls.append(cmd))
            import plistlib
            self.assertEqual(plistlib.loads(target_mac.read_bytes())['ProgramArguments'],[str(launcher.resolve())])
            self.assertEqual(calls[-1][0:2],['launchctl','bootstrap'])

    def test_tailscale_certificate_generation_never_replaces_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)/'data';hostname='kidoo.tail123.ts.net'; calls=[]
            def fake_tailscale(command,check):
                calls.append(command)
                cert=command[command.index('--cert-file')+1];key=command[command.index('--key-file')+1]
                subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                                '-subj','/CN='+hostname,'-keyout',key,'-out',cert],
                               check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            cert,key=setup_wizard.issue_tailscale_certificate(hostname,data,run=fake_tailscale)
            self.assertEqual(cert.stat().st_mode & 0o777,0o600)
            self.assertEqual(key.stat().st_mode & 0o777,0o600)
            self.assertEqual(len(calls),1)
            with self.assertRaises(FileExistsError): setup_wizard.issue_tailscale_certificate(hostname,data,run=fake_tailscale)
            self.assertEqual(len(calls),1)
            with self.assertRaises(ValueError): setup_wizard.issue_tailscale_certificate('not-a-tailnet.example',data,run=fake_tailscale)
