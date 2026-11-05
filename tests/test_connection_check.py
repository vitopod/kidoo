"""Exercise certificate, hostname, route and protected-mode checks on loopback."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import connection_check


class InfoHandler(BaseHTTPRequestHandler):
    protected=True
    product='Kidoo'
    def log_message(self,*args):pass
    def do_GET(self):
        body=json.dumps({'product':self.product,'secureParentRoles':self.protected}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)


class ConnectionCheckTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name);self.cert=root/'cert.pem';key=root/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost','-keyout',str(key),'-out',str(self.cert)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),InfoHandler)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(self.cert,key);self.server.socket=ctx.wrap_socket(self.server.socket,server_side=True)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.context=ssl.create_default_context(cafile=str(self.cert));self.url=f'https://localhost:{self.server.server_port}'
    def tearDown(self):
        InfoHandler.protected=True;InfoHandler.product='Kidoo';self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def test_trusted_certificate_hostname_and_private_route(self):
        self.assertTrue(connection_check.check(self.url,'127.0.0.1',context=self.context)['ok'])
    def test_untrusted_or_wrong_hostname_fails_closed(self):
        with self.assertRaisesRegex(connection_check.ConnectionCheckError,'certificate'):
            connection_check.check(self.url,'127.0.0.1')
        with self.assertRaisesRegex(connection_check.ConnectionCheckError,'certificate'):
            connection_check.check(f'https://127.0.0.1:{self.server.server_port}',context=self.context)
    def test_legacy_mode_and_bad_addresses_fail(self):
        InfoHandler.protected=False
        with self.assertRaisesRegex(connection_check.ConnectionCheckError,'protected mode'):
            connection_check.check(self.url,'127.0.0.1',context=self.context)
        for url,ip in [('http://localhost',None),('https://user:secret@localhost',None),('https://localhost/path',None),(self.url,'8.8.8.8')]:
            with self.assertRaises(connection_check.ConnectionCheckError):connection_check.check(url,ip,context=self.context)
    def test_unverified_context_is_rejected(self):
        with self.assertRaisesRegex(connection_check.ConnectionCheckError,'verification are required'):
            connection_check.check(self.url,context=ssl._create_unverified_context())
    def test_other_https_server_is_not_mistaken_for_kidoo(self):
        InfoHandler.product='Another service'
        with self.assertRaisesRegex(connection_check.ConnectionCheckError,'identify itself as Kidoo'):
            connection_check.check(self.url,'127.0.0.1',context=self.context)
    def test_missing_python_ca_store_has_actionable_error(self):
        empty=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with patch('connection_check.ssl.create_default_context',return_value=empty),patch('connection_check.ssl.get_default_verify_paths',return_value=SimpleNamespace(capath=None)):
            with self.assertRaisesRegex(connection_check.ConnectionCheckError,'Python has no trusted CA certificates'):
                connection_check.check(self.url,'127.0.0.1')

if __name__=='__main__':unittest.main()
