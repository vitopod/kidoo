"""Community home checks using metadata only; no media files or network sources."""
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from kidoo_server import Handler, init_db


class CommunityHomeTest(unittest.TestCase):
    def test_home_does_not_read_online_catalogue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / 'media'
            media.mkdir()
            database = root / 'test.db'
            init_db(database)
            server = SimpleNamespace(db_path=str(database), media_dir=media, protected_mode=False)
            # A read is a regression even when the online catalogue is empty.
            server.catalogue = lambda: self.fail('home read the online catalogue')
            handler = object.__new__(Handler)
            handler.server = server
            handler.path = '/api/tv/home/family?suggestionSession=1'
            handler.send_json = lambda value, *args: value
            with patch('kidoo_server.jellyfin.load_config', return_value={}):
                home = handler.native_home('family')
            self.assertNotIn('catalogue',home)
            self.assertNotIn('youtube',home['sections'])
            self.assertFalse(any(row['id'] == 'youtube' for row in home['homeShelves']))

    def test_approved_jellyfin_title_and_resume_are_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / 'media'
            media.mkdir()
            database = root / 'test.db'
            init_db(database)
            with sqlite3.connect(database) as db:
                db.execute('INSERT INTO jellyfin_approvals (server,item_id) VALUES (?,?)', ('fixture', 'movie'))
            server = SimpleNamespace(db_path=str(database), media_dir=media, protected_mode=False)
            server.catalogue = lambda: self.fail('home read the online catalogue')
            handler = object.__new__(Handler)
            handler.server = server
            handler.path = '/api/tv/home/family'
            handler.send_json = lambda value, *args: value
            item = {'id':'jellyfin:movie', 'title':'Example movie', 'type':'Movie',
                    'duration':600, 'progress':{'position':100,'completed':False},
                    'genres':['Adventure'], 'hasArtwork':False}
            with patch('kidoo_server.jellyfin.load_config', return_value={'family':{'server':'fixture'}}), \
                 patch('kidoo_server.jellyfin.list_items', return_value={'items':[item], 'total':1}), \
                 patch('kidoo_server.jellyfin.list_series_artwork', return_value={}):
                home = handler.native_home('family')
            self.assertEqual([v['title'] for v in home['sections']['movies']], ['Example movie'])
            resume = next(row for row in home['rows'] if row['id'] == 'continue')
            self.assertEqual(resume['items'][0]['progress']['position'], 100)


if __name__ == '__main__':
    unittest.main()
