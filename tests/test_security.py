import concurrent.futures
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from security import SecurityError, SecurityStore


class SecurityStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/'security.db'
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE profiles(id TEXT PRIMARY KEY, name TEXT NOT NULL)')
            db.execute("INSERT INTO profiles VALUES ('family','Family')")
            db.execute("INSERT INTO profiles VALUES ('other','Other')")
        self.clock=1000
        self.store=SecurityStore(self.db,now=lambda:self.clock)
        self.store.set_pin('867530')

    def tearDown(self): self.tmp.cleanup()

    def paired(self,profile='family'):
        return self.store.redeem_pairing(self.store.create_pairing(profile,'Living room'))

    def test_passwordless_administrator_record_is_unique_and_does_not_unlock_a_device(self):
        account=self.store.create_administrator('Example parent')
        self.assertEqual(account['role'],'administrator')
        self.assertFalse(account['hasPassword'])
        with sqlite3.connect(self.db) as db:
            row=db.execute('SELECT name,password_verifier FROM security_administrators WHERE id=?',(account['id'],)).fetchone()
        self.assertEqual(row,('Example parent',None))
        with self.assertRaises(SecurityError):self.store.create_administrator('example parent')
        with self.assertRaises(SecurityError):self.store.create_administrator('  ')
        self.assertIsNone(self.store.device(account['id']))

    def test_credential_is_profile_scoped_persistent_and_only_hashes_are_stored(self):
        paired=self.paired();token=paired['credential']
        self.assertEqual(self.store.device(token)['profileId'],'family')
        self.assertEqual(SecurityStore(self.db).device(token)['profileId'],'family')
        with sqlite3.connect(self.db) as db:
            self.assertNotIn(token.encode(),db.execute('SELECT credential_hash FROM security_devices').fetchone()[0])
            self.assertNotIn(b'867530',db.execute('SELECT verifier FROM security_pin').fetchone()[0])
        with self.assertRaises(SecurityError):self.store.create_pairing('unknown','TV')
        with sqlite3.connect(self.db) as db:db.execute("DELETE FROM profiles WHERE id='family'")
        self.assertIsNone(self.store.device(token))

    def test_pairing_is_expiring_single_use_and_atomic(self):
        code=self.store.create_pairing('family','TV',ttl=10)
        def redeem(_):
            try:return self.store.redeem_pairing(code)
            except SecurityError:return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(redeem,range(2)))
        self.assertEqual(sum(x is not None for x in results),1)
        code=self.store.create_pairing('family','TV',ttl=10);self.clock+=10
        with self.assertRaises(SecurityError):self.store.redeem_pairing(code)
        code=self.store.create_pairing('other','TV')
        with sqlite3.connect(self.db) as db:db.execute("DELETE FROM profiles WHERE id='other'")
        with self.assertRaises(SecurityError):self.store.redeem_pairing(code)

    def test_parent_unlock_is_device_scoped_expires_and_revokes(self):
        one=self.paired();two=self.paired('other')
        session=self.store.unlock(one['credential'],'867530',ttl=10)
        self.assertTrue(self.store.is_unlocked(one['credential'],session))
        self.assertFalse(self.store.is_unlocked(two['credential'],session))
        self.assertEqual(len(self.store.devices()),2)
        self.store.lock(two['credential'],session)
        self.assertTrue(self.store.is_unlocked(one['credential'],session))
        self.clock+=10;self.assertFalse(self.store.is_unlocked(one['credential'],session))
        session=self.store.unlock(one['credential'],'867530')
        self.store.lock(one['credential'],session)
        self.assertFalse(self.store.is_unlocked(one['credential'],session))
        session=self.store.unlock(one['credential'],'867530')
        self.store.revoke_device(one['deviceId'])
        self.assertIsNone(self.store.device(one['credential']))
        self.assertFalse(self.store.is_unlocked(one['credential'],session))
        self.assertIsNotNone(self.store.device(two['credential']))
        self.assertEqual([d['id'] for d in self.store.devices()],[two['deviceId']])

    def test_profile_approvals_and_explicit_legacy_import_are_isolated(self):
        with sqlite3.connect(self.db) as db:
            db.executescript("""
            CREATE TABLE approvals(media_id TEXT PRIMARY KEY,approved INTEGER NOT NULL);
            CREATE TABLE discovery_video_approvals(video_id INTEGER PRIMARY KEY);
            CREATE TABLE discovery_channel_approvals(channel_id TEXT PRIMARY KEY);
            CREATE TABLE jellyfin_approvals(server TEXT NOT NULL,item_id TEXT NOT NULL);
            INSERT INTO approvals VALUES ('local-one',1),('local-hidden',0);
            INSERT INTO discovery_video_approvals VALUES (16);
            INSERT INTO discovery_channel_approvals VALUES ('channel-one');
            INSERT INTO jellyfin_approvals VALUES ('server-one','item-one'),('server-two','item-two');
            """)
        self.assertFalse(self.store.is_approved('family','youtube','16'))
        self.store.import_legacy_approvals('family','server-one')
        self.assertTrue(self.store.is_approved('family','local','local-one'))
        self.assertFalse(self.store.is_approved('family','local','local-hidden'))
        self.assertFalse(self.store.is_approved('family','youtube','16'))
        self.assertFalse(self.store.is_approved('family','channel','channel-one'))
        self.assertTrue(self.store.is_approved('family','jellyfin','item-one','server-one'))
        self.assertFalse(self.store.is_approved('family','jellyfin','item-two','server-two'))
        self.assertFalse(self.store.is_approved('other','youtube','16'))
        with self.assertRaises(SecurityError):self.store.set_approval('family','youtube','16',False)
        with self.assertRaises(SecurityError):self.store.import_legacy_approvals('family','server-one')
        self.assertFalse(self.store.is_approved('family','youtube','16'))
        with self.assertRaises(SecurityError):self.store.set_approval('other','youtube','16',True)
        with self.assertRaises(SecurityError):self.store.set_approval('other','jellyfin','item',True)

    def test_pin_rate_limit_and_reset_invalidate_parent_sessions(self):
        device=self.paired();credential=device['credential']
        session=self.store.unlock(credential,'867530')
        for _ in range(5):
            with self.assertRaises(SecurityError):self.store.unlock(credential,'111111')
        with self.assertRaisesRegex(SecurityError,'later'):self.store.unlock(credential,'867530')
        self.clock+=60
        self.assertTrue(self.store.is_unlocked(credential,self.store.unlock(credential,'867530')))
        self.store.set_pin('1234567')
        self.assertFalse(self.store.is_unlocked(credential,session))
        self.assertTrue(self.store.is_unlocked(credential,self.store.unlock(credential,'1234567')))
        for invalid in ('12345','abcdef','１２３４５６'):
            with self.assertRaises(SecurityError):self.store.set_pin(invalid)

class ProtectedStartupTest(unittest.TestCase):
    def test_protected_mode_refuses_unencrypted_or_unconfigured_startup(self):
        server=Path(__file__).resolve().parents[1]/'server'/'kidoo_server.py'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);media=root/'media';media.mkdir()
            base=[sys.executable,str(server),'--media-dir',str(media),'--data-dir',str(root/'data'),'--port','0','--protected-mode']
            no_tls=subprocess.run(base,capture_output=True,text=True,timeout=10)
            self.assertNotEqual(no_tls.returncode,0);self.assertIn('requires a trusted TLS certificate',no_tls.stderr)
            unconfigured=subprocess.run(base+['--tls-cert','unused','--tls-key','unused'],capture_output=True,text=True,timeout=10)
            self.assertNotEqual(unconfigured.returncode,0);self.assertIn('Set a parent PIN and create an administrator',unconfigured.stderr)

if __name__=='__main__':unittest.main()
