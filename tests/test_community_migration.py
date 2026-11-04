import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import community_migration
from kidoo_server import init_db
from security import SecurityStore


class CommunityMigrationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / 'source.db'
        self.target = self.root / 'community.db'
        with sqlite3.connect(self.source) as db:
            db.executescript('''CREATE TABLE your_tv_definitions(profile_id TEXT,id TEXT,definition TEXT);
                CREATE TABLE your_tv_queue(profile_id TEXT,channel_id TEXT,item_key TEXT,youtube_id TEXT,position INTEGER);
                CREATE TABLE progress(profile_id TEXT,media_id TEXT,position REAL);
                CREATE TABLE security_profile_approvals(profile_id TEXT,kind TEXT,namespace TEXT,item_id TEXT);
                CREATE TABLE viewing_limits(profile_id TEXT,total INTEGER,movies INTEGER);
                CREATE TABLE activity_sessions(profile_id TEXT,kind TEXT,item_id TEXT,watched_seconds REAL);''')
            channels = [
                ('mixed', ['youtube', 'jellyfin']),
                ('online-only', ['youtube']),
            ]
            for ident, sources in channels:
                data = {'id': ident, 'name': ident, 'topics': ['music'], 'languages': [],
                        'sources': sources, 'enabled': True}
                db.execute('INSERT INTO your_tv_definitions VALUES (?,?,?)', ('profile', ident, json.dumps(data)))
            db.executemany('INSERT INTO your_tv_queue VALUES (?,?,?,?,?)', [
                ('profile', 'mixed', 'jellyfin:one', '', 0),
                ('profile', 'mixed', 'online:123', 'exampleid01', 1),
                ('profile', 'mixed', 'local:two', '', 2),
            ])
            db.execute('INSERT INTO progress VALUES (?,?,?)', ('profile', 'youtube:123', 42))
            db.execute('INSERT INTO progress VALUES (?,?,?)', ('profile', 'family-film', 315))
            db.execute('INSERT INTO security_profile_approvals VALUES (?,?,?,?)', ('profile','local','','family-film'))
            db.execute('INSERT INTO viewing_limits VALUES (?,?,?)', ('profile',120,90))
            db.execute('INSERT INTO activity_sessions VALUES (?,?,?,?)', ('profile','local','family-film',120))

    def tearDown(self):
        self.directory.cleanup()

    def test_copy_preserves_source_and_legacy_history(self):
        self.assertEqual(community_migration.create_copy(self.source, self.target), 2)
        with sqlite3.connect(self.source) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM your_tv_queue').fetchone()[0], 3)
            self.assertIn('youtube', json.loads(db.execute('SELECT definition FROM your_tv_definitions WHERE id=?', ('mixed',)).fetchone()[0])['sources'])
        with sqlite3.connect(self.target) as db:
            mixed = json.loads(db.execute('SELECT definition FROM your_tv_definitions WHERE id=?', ('mixed',)).fetchone()[0])
            online = json.loads(db.execute('SELECT definition FROM your_tv_definitions WHERE id=?', ('online-only',)).fetchone()[0])
            self.assertEqual(mixed['sources'], ['jellyfin'])
            self.assertTrue(mixed['enabled'])
            self.assertFalse(online['enabled'])
            self.assertEqual(db.execute('SELECT item_key FROM your_tv_queue ORDER BY position').fetchall(), [('jellyfin:one',), ('local:two',)])
            self.assertEqual(db.execute('SELECT position FROM progress WHERE media_id=?', ('youtube:123',)).fetchone()[0], 42)
            self.assertEqual(db.execute('SELECT position FROM progress WHERE media_id=?', ('family-film',)).fetchone()[0], 315)
            self.assertEqual(db.execute('SELECT kind,item_id FROM security_profile_approvals').fetchall(), [('local','family-film')])
            self.assertEqual(db.execute('SELECT total,movies FROM viewing_limits').fetchone(), (120,90))
            self.assertEqual(db.execute('SELECT kind,item_id,watched_seconds FROM activity_sessions').fetchone(), ('local','family-film',120))
            db.execute('BEGIN IMMEDIATE')
            community_migration.convert_channels(db)
            db.commit()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM your_tv_queue').fetchone()[0], 2)

    def test_existing_destination_is_never_overwritten(self):
        self.target.write_bytes(b'keep')
        with self.assertRaises(ValueError):
            community_migration.create_copy(self.source, self.target)
        self.assertEqual(self.target.read_bytes(), b'keep')

    def test_invalid_channel_rolls_back_copy_and_preserves_source(self):
        with sqlite3.connect(self.source) as db:
            db.execute('UPDATE your_tv_definitions SET definition=? WHERE id=?', ('not json', 'online-only'))
        with self.assertRaises(RuntimeError):
            community_migration.create_copy(self.source, self.target)
        with sqlite3.connect(self.target) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM your_tv_queue').fetchone()[0], 3)
            mixed = json.loads(db.execute('SELECT definition FROM your_tv_definitions WHERE id=?', ('mixed',)).fetchone()[0])
            self.assertIn('youtube', mixed['sources'])

class OfflineUpgradeTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.database=self.root/'kidoo.db';self.backup=self.root/'private-backup.db'
        init_db(self.database);self.security=SecurityStore(self.database)
        self.security.set_approval('family','local','film',True)
        self.device=self.security.redeem_pairing(self.security.create_pairing('family','Example TV'))
        legacy={'id':'custom','name':'Custom','topics':['all'],'languages':[],'sources':['youtube','local'],'enabled':True}
        with sqlite3.connect(self.database) as db:
            db.execute('INSERT INTO your_tv_definitions VALUES (?,?,?)',('family','custom',json.dumps(legacy)))
            db.execute('INSERT INTO progress VALUES (?,?,?,?,?,?)',('family','film',37,100,0,10))
            db.execute('INSERT INTO viewing_limits(profile_id,total,movies) VALUES (?,?,?)',('family',60,40))
            db.execute('INSERT INTO your_tv_queue VALUES (?,?,?,?,?,?)',('queue','family','custom','online:1','exampleid01',0))
    def tearDown(self): self.temp.cleanup()

    def test_upgrade_keeps_backup_credentials_progress_and_is_repeatable(self):
        count=community_migration.upgrade_stopped(self.database,self.backup,server_stopped=True)
        self.assertEqual(count,1)
        with sqlite3.connect(self.backup) as db:
            self.assertEqual(json.loads(db.execute('SELECT definition FROM your_tv_definitions').fetchone()[0])['sources'],['youtube','local'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM your_tv_queue').fetchone()[0],1)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(json.loads(db.execute('SELECT definition FROM your_tv_definitions').fetchone()[0])['sources'],['local'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM your_tv_queue').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT position FROM progress').fetchone()[0],37)
            self.assertEqual(db.execute('SELECT total,movies FROM viewing_limits').fetchone(),(60,40))
            self.assertTrue(db.execute('SELECT 1 FROM schema_flags WHERE name=?',(community_migration.VERSION,)).fetchone())
        self.assertIsNotNone(SecurityStore(self.database).device(self.device['credential']))
        self.assertTrue(SecurityStore(self.database).is_approved('family','local','film'))
        init_db(self.database)  # A normal Community restart preserves the migrated state.
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute('SELECT position FROM progress').fetchone()[0],37)
        self.assertEqual(community_migration.upgrade_stopped(self.database,self.root/'second-backup.db',server_stopped=True),1)

    def test_must_stop_and_never_overwrite_backup(self):
        with self.assertRaises(ValueError): community_migration.upgrade_stopped(self.database,self.backup)
        self.backup.write_text('do not overwrite')
        with self.assertRaises(ValueError): community_migration.upgrade_stopped(self.database,self.backup,server_stopped=True)
        self.assertEqual(self.backup.read_text(),'do not overwrite')

    def test_refuses_sqlite_sidecars(self):
        Path(str(self.database)+'-wal').write_text('pending')
        with self.assertRaisesRegex(ValueError,'sidecar'): community_migration.upgrade_stopped(self.database,self.backup,server_stopped=True)
        self.assertFalse(self.backup.exists())

    def test_interruption_before_atomic_replace_keeps_original_and_backup(self):
        with patch('community_migration.os.replace',side_effect=OSError('interrupted')):
            with self.assertRaises(RuntimeError): community_migration.upgrade_stopped(self.database,self.backup,server_stopped=True)
        for path in (self.database,self.backup):
            with sqlite3.connect(path) as db:
                self.assertEqual(json.loads(db.execute('SELECT definition FROM your_tv_definitions').fetchone()[0])['sources'],['youtube','local'])

    def test_source_change_refuses_replacement_and_keeps_backup(self):
        with patch('community_migration._unchanged',return_value=False):
            with self.assertRaises(RuntimeError):community_migration.upgrade_stopped(self.database,self.backup,server_stopped=True)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(json.loads(db.execute('SELECT definition FROM your_tv_definitions').fetchone()[0])['sources'],['youtube','local'])
        self.assertTrue(self.backup.exists())

    def test_unknown_schema_is_rejected_before_backup(self):
        other=self.root/'other.db'
        with sqlite3.connect(other) as db:db.execute('CREATE TABLE other(id INTEGER)')
        with self.assertRaisesRegex(ValueError,'Unsupported database schema'):
            community_migration.upgrade_stopped(other,self.backup,server_stopped=True)
        self.assertFalse(self.backup.exists())


if __name__ == '__main__':
    unittest.main()
