import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import backup


class BackupTest(unittest.TestCase):
    def test_snapshot_restores_database_and_private_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); data = root / 'data'; data.mkdir()
            with sqlite3.connect(data / 'kidoo.db') as db:
                db.execute('CREATE TABLE progress (position INTEGER)')
                db.execute('INSERT INTO progress VALUES (37)')
            (data / 'jellyfin.json').write_text('{"example": true}')
            (data / 'tls').mkdir(); (data / 'tls/server.key').write_text('private')
            snapshot = backup.create(data, root / 'snapshot')
            with sqlite3.connect(data / 'kidoo.db') as db: db.execute('UPDATE progress SET position=99')
            restored = backup.restore(snapshot, root / 'restored')
            with sqlite3.connect(restored / 'kidoo.db') as db:
                self.assertEqual(db.execute('SELECT position FROM progress').fetchone()[0], 37)
            self.assertEqual((restored / 'jellyfin.json').read_text(), '{"example": true}')
            self.assertEqual((restored / 'tls/server.key').read_text(), 'private')
            self.assertEqual((restored / 'tls/server.key').stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError): backup.restore(snapshot, restored)
            with self.assertRaises(ValueError): backup.create(data, data / 'nested')


if __name__ == '__main__': unittest.main()
