"""Browser cookies enforce the same child boundary as paired devices."""
from http.cookies import SimpleCookie
import http.client
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from kidoo_server import KidooServer


class BrowserChildTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name);self.media=root/'media';self.media.mkdir()
        self.server=KidooServer(('127.0.0.1',0),self.media,root/'test.db','test')
        self.server.protected_mode=True
        self.secure=patch('kidoo_server.Handler.secure_transport',return_value=True);self.secure.start()
        self.items=patch('kidoo_server.media_index',return_value={'allowed':{'id':'allowed','title':'Allowed','path':self.media/'not-a-video'},'hidden':{'id':'hidden','title':'Hidden','path':self.media/'not-a-video'}});self.items.start()
        self.mapping=patch('kidoo_server.jellyfin.load_config',return_value={});self.mapping.start()
        self.server.security.create_administrator('Parent');self.server.security.set_pin('482951')
        self.server.sessions['old-parent']=10**12
        self.server.security.set_approval('family','local','allowed',True)
        with sqlite3.connect(self.server.db_path) as db:db.execute("INSERT INTO profiles VALUES ('other','Other')")
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        self.secure.stop();self.items.stop();self.mapping.stop();self.temp.cleanup()
    def request(self,method,path,data=None,cookie='',header=True,origin=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5);headers={}
        if cookie:headers['Cookie']=cookie
        if header:headers['X-Kidoo-Browser']='1'
        if origin:headers['Origin']=origin
        if data is not None:headers['Content-Type']='application/json'
        connection.request(method,path,None if data is None else json.dumps(data),headers)
        response=connection.getresponse();status=response.status;cookies=response.headers.get_all('Set-Cookie') or [];body=json.loads(response.read());connection.close()
        return status,body,cookies
    def pair(self):
        code=self.server.security.create_pairing('family','Browser')
        status,body,cookies=self.request('POST','/api/browser/pair',{'code':code},cookie='kidoo_session=old-parent')
        self.assertEqual(status,200);self.assertNotIn('credential',body)
        self.assertNotIn('old-parent',self.server.sessions)
        token=next(c for c in cookies if c.startswith('__Host-kidoo_child='))
        self.assertIn('Secure',token);self.assertIn('HttpOnly',token);self.assertIn('SameSite=Strict',token)
        return token.split(';',1)[0]
    def test_pairing_and_profile_boundary_cannot_fall_back_to_parent_cookie(self):
        cookie=self.pair()+'; kidoo_session=old-parent'
        self.assertEqual(self.request('GET','/api/tv/home/family',cookie=cookie)[0],200)
        self.assertEqual(self.request('GET','/api/tv/home/other',cookie=cookie)[0],403)
        self.assertEqual(self.request('GET','/media/hidden',cookie=cookie)[0],403)
        home=self.request('GET','/api/tv/home/family',cookie=cookie)[1]
        self.assertEqual([v['id'] for v in home['local']],['allowed'])
        self.assertEqual(self.request('GET','/api/security/devices',cookie=cookie)[0],401)
        self.assertIn(self.request('POST','/api/security/approval',{'profileId':'family','kind':'local','itemId':'hidden','approved':True},cookie=cookie)[0],(401,403))
        self.assertEqual(self.request('POST','/api/activity/start',{'profileId':'other','kind':'local','itemId':'allowed'},cookie=cookie)[0],403)
        self.assertEqual(self.request('POST','/api/activity/start',{'profileId':'family','kind':'local','itemId':'allowed'},cookie=cookie,header=False)[0],403)
        self.assertEqual(self.request('POST','/api/browser/unlock',{'pin':'482951'},cookie=cookie,origin='https://attacker.invalid')[0],403)
        self.assertEqual(self.request('POST','/api/browser/unlock',{'pin':'wrong'},cookie=cookie)[0],403)
        status,_,headers=self.request('POST','/api/browser/unlock',{'pin':'482951'},cookie=cookie)
        self.assertEqual(status,200);parent=next(c for c in headers if c.startswith('__Host-kidoo_parent=')).split(';',1)[0]
        unlocked=cookie+'; '+parent
        self.assertEqual(self.request('GET','/api/security/devices',cookie=unlocked)[0],200)
        self.assertEqual(self.request('POST','/api/browser/lock',{},cookie=unlocked)[0],200)
        self.assertEqual(self.request('GET','/api/security/devices',cookie=unlocked)[0],401)
        with sqlite3.connect(self.server.db_path) as db: device_id=db.execute('SELECT id FROM security_devices').fetchone()[0]
        self.server.security.revoke_device(device_id)
        self.assertEqual(self.request('GET','/api/tv/home/family',cookie=cookie)[0],401)
    def test_pairing_requires_protected_https_and_csrf_header(self):
        code=self.server.security.create_pairing('family','Browser')
        self.assertEqual(self.request('POST','/api/browser/pair',{'code':code},header=False)[0],403)
        with patch('kidoo_server.Handler.secure_transport',return_value=False):
            self.assertEqual(self.request('POST','/api/browser/pair',{'code':code})[0],403)
        self.assertEqual(self.request('POST','/api/browser/pair',{'code':code})[0],200)
        self.assertEqual(self.request('POST','/api/browser/pair',{'code':code})[0],403)

    def test_parent_unlock_is_rate_limited(self):
        cookie=self.pair()
        for _ in range(5):self.assertEqual(self.request('POST','/api/browser/unlock',{'pin':'000000'},cookie=cookie)[0],403)
        self.assertEqual(self.request('POST','/api/browser/unlock',{'pin':'482951'},cookie=cookie)[0],403)


if __name__=='__main__':unittest.main()
