#!/usr/bin/env python3
"""Run an isolated Kidoo dashboard with fictional viewing records.

This creates no video files, reads no family data and uses no real online video
titles or thumbnails. The demonstration records cannot be played.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
import jellyfin
import kidoo_server
from kidoo_server import KidooServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=14320)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='kidoo-demo-') as directory:
        root = Path(directory).resolve()
        media = root / 'media'
        media.mkdir()
        # Prevent live catalogue or channel-feed requests, including thumbnails.
        server = KidooServer(('127.0.0.1', args.port), media, root / 'kidoo.db', '')
        now = time.time()
        with sqlite3.connect(server.db_path) as db:
            db.execute('UPDATE profiles SET name=? WHERE id=?', ('Example profile', 'family'))
            events = [
                ('demo-explorer', 'jellyfin', 'explorer', 650, 1800, 0, 'movies'),
                ('demo-garden', 'jellyfin', 'garden', 720, 1200, 1, 'series'),
                ('demo-film', 'jellyfin', 'stars', 3000, 3600, 2, 'movies'),
                ('demo-encore', 'jellyfin', 'explorer', 480, 1800, 4, 'movies'),
            ]
            for ident, kind, item, seconds, duration, days, category in events:
                at = now - days * 86400 - 3600
                db.execute('''INSERT INTO activity_sessions
                    (id,profile_id,kind,item_id,started_at,updated_at,watched_seconds,duration,position,sequence,content_category)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (ident, 'family', kind, item, at-seconds, at, seconds, duration, seconds, 1, category))
                db.execute('INSERT INTO activity_minutes VALUES (?,?,?)', (ident, int(at//60), seconds))
            db.execute('INSERT INTO content_reviews VALUES (?,?,?,?,?,?)',
                       ('family','jellyfin','stars','discuss','A story to talk about together.',now))
            db.execute('INSERT INTO viewing_limits(profile_id,total,movies,youtube) VALUES (?,?,?,?)',
                       ('family', 120, 90, 45))
        # Fake Jellyfin metadata is supplied in-process; no external server is contacted.
        jellyfin.load_config = lambda _: {'family': {'server': 'http://example.invalid', 'userId': 'demo', 'token': 'demo'}}
        def listing(mapping, start=0, limit=1000, resume=False, **kwargs):
            examples = [('explorer','A Family Adventure','Movie',1800,['Adventure','Family']),
                        ('garden','Garden Discoveries','Episode',1200,['Nature']),
                        ('stars','A Journey to the Stars','Movie',3600,['Space','Adventure'])]
            items = [{'id':'jellyfin:'+ident, 'title':title, 'type':kind,
                      'overview':'An example title for the Kidoo demonstration.',
                      'series':None, 'seriesId':None, 'duration':duration, 'rating':None,
                      'genres':genres, 'year':None, 'hasArtwork':False,
                      'progress':{'position':min(650,duration), 'completed':False, 'playCount':1,
                                  'updatedAt':datetime.fromtimestamp(now-86400,timezone.utc).isoformat()}}
                     for ident,title,kind,duration,genres in examples]
            return {'items':items[start:start+limit], 'total':len(items)}
        jellyfin.list_items = listing
        jellyfin.get_item = lambda mapping, ident: {'Id':ident,'Name':'Example title','Type':'Movie'}
        print(f'Demo dashboard: http://127.0.0.1:{args.port}/dashboard', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
