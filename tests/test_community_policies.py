"""Community selection, limits and Jellyfin policy without media files."""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import jellyfin
from kidoo_server import init_db
import time_limits
import tv_catalogue
import your_tv

class CommunityPolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Path(self.tmp.name)/'db.sqlite';init_db(self.db)
    def tearDown(self): self.tmp.cleanup()
    def test_queue_retains_order_refills_and_rechecks_eligibility(self):
        definition={'id':'animals','topics':['animals'],'sources':['local'],'languages':[]}
        items=[{'id':str(n),'key':'local:'+str(n),'kind':'local','title':str(n),'tags':['animals'],'approved':True} for n in range(5)]
        with sqlite3.connect(self.db) as db:
            first=your_tv.reconcile_queue(db,'family',definition,items)
            keys=[v['key'] for v in first];self.assertEqual(len(keys),3)
            self.assertEqual([v['key'] for v in your_tv.reconcile_queue(db,'family',definition,items)],keys)
            db.execute('DELETE FROM your_tv_queue WHERE item_key=?',(keys[0],))
            refill=your_tv.reconcile_queue(db,'family',definition,items,recent=[keys[0]])
            self.assertEqual([v['key'] for v in refill[:2]],keys[1:]);self.assertNotEqual(refill[2]['key'],keys[0])
            remaining=[v for v in items if v['key']!=keys[1]]
            self.assertNotIn(keys[1],[v['key'] for v in your_tv.reconcile_queue(db,'family',definition,remaining)])
    def test_unsupported_items_never_match_even_in_legacy_channel(self):
        legacy={'topics':['all'],'sources':['youtube','local'],'languages':[]}
        self.assertFalse(your_tv.matches(legacy,{'kind':'external','key':'online:1'}))
        self.assertTrue(your_tv.matches(legacy,{'kind':'local','key':'local:1'}))
    def test_sessions_and_offers_are_bound_to_profile_and_device(self):
        with sqlite3.connect(self.db) as db:
            session=your_tv.start(db,'family','device-a','surprise')
            self.assertEqual(your_tv.get_session(db,session,'family','device-a')[0],'surprise')
            with self.assertRaises(ValueError):your_tv.get_session(db,session,'family','device-b')
            with self.assertRaises(ValueError):your_tv.get_session(db,session,'other','device-a')
            token=your_tv.offer(db,'family','device-a','surprise','local:one',now=100)
            self.assertEqual(your_tv.offered_item(db,token,'family','device-a','surprise',now=101),'local:one')
            self.assertIsNone(your_tv.offered_item(db,token,'family','device-b','surprise',now=101))
            self.assertIsNone(your_tv.offered_item(db,token,'family','device-a','surprise',now=30000))
    def test_show_less_and_watched_items_are_not_promoted(self):
        item={'id':'1','key':'jellyfin:1','kind':'jellyfin','title':'One','approved':True,'mediaType':'Movie','genres':['Adventure'],'progress':{'position':12}}
        self.assertTrue(tv_catalogue.deprioritized_library(item,{('jellyfin','1')}))
        self.assertEqual(tv_catalogue.recommend_jellyfin([item],[],{'interests':['stories']},set(),{},4),[])
    def test_limits_midnight_and_daylight_saving(self):
        previous=os.environ.get('TZ');os.environ['TZ']='Europe/Zurich';time.tzset()
        try:
            spring=time.mktime((2026,3,29,12,0,0,0,0,-1));fall=time.mktime((2026,10,25,12,0,0,0,0,-1))
            self.assertEqual(time_limits.bounds(spring)[1]-time_limits.bounds(spring)[0],23*3600)
            self.assertEqual(time_limits.bounds(fall)[1]-time_limits.bounds(fall)[0],25*3600)
            now=time.mktime((2026,11,3,23,59,30,0,0,-1));tomorrow=time.mktime((2026,11,4,0,0,30,0,0,-1))
            with sqlite3.connect(self.db) as db:
                db.execute("INSERT INTO viewing_limits(profile_id,total,movies) VALUES ('family',2,1)")
                db.execute('INSERT INTO activity_sessions(id,profile_id,kind,item_id,started_at,updated_at,content_category) VALUES (?,?,?,?,?,?,?)',('activity','family','jellyfin','item',now-60,now,'movies'))
                db.execute('INSERT INTO activity_minutes VALUES (?,?,?)',('activity',int(now//60),60))
                self.assertTrue(time_limits.status(db,'family','movies',now)['blocked'])
                self.assertFalse(time_limits.status(db,'family','movies',tomorrow)['blocked'])
        finally:
            if previous is None:os.environ.pop('TZ',None)
            else:os.environ['TZ']=previous
            time.tzset()
    def test_jellyfin_transcode_paths_cannot_escape_item(self):
        self.assertEqual(jellyfin.safe_transcode_path('/videos/abc123/master.m3u8?ApiKey=secret&quality=1','abc123'),'/videos/abc123/master.m3u8?quality=1')
        for value in ('https://evil.example/videos/abc123/master.m3u8','/videos/other/master.m3u8','/videos/abc123/%2e%2e/other.ts'):
            with self.assertRaises(ValueError):jellyfin.safe_transcode_path(value,'abc123')
    def test_approved_series_artwork_requires_matching_episode(self):
        mapping={'server':'http://example.invalid','userId':'test','token':'test'}
        pages=[{'Items':[{'Id':'other','Type':'Episode','SeriesId':'series1'}],'TotalRecordCount':2},
               {'Items':[{'Id':'approved','Type':'Episode','SeriesId':'series1'}],'TotalRecordCount':2}]
        with patch('jellyfin.request',side_effect=pages):
            self.assertTrue(jellyfin.series_has_approved_episode(mapping,'series1',{'approved'}))

if __name__=='__main__':unittest.main()
