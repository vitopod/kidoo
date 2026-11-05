"""Isolated HTTP boundary tests; metadata only, no video or external network."""
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

class CommunityBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        root=Path(self.temp.name); self.media=root/'media'; self.media.mkdir()
        self.server=KidooServer(('127.0.0.1',0),self.media,root/'test.db','parent')
        self.server.protected_mode=True
        self.patch=patch('kidoo_server.Handler.secure_transport',return_value=True);self.patch.start()
        self.index=patch('kidoo_server.media_index',return_value={'allowed':{'id':'allowed','title':'Allowed','path':self.media/'missing'}});self.index.start()
        self.mapping=patch('kidoo_server.jellyfin.load_config',return_value={});self.mapping.start()
        self.server.sessions['test-parent']=10**12
        self.server.security.set_approval('family','local','allowed',True)
        with sqlite3.connect(self.server.db_path) as db: db.execute("INSERT INTO profiles VALUES ('other','Other')")
        code=self.server.security.create_pairing('family','Test device')
        self.device=self.server.security.redeem_pairing(code)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        self.mapping.stop();self.index.stop();self.patch.stop();self.temp.cleanup()
    def request(self, method, path, data=None, child=True, browser=False):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        headers={'Authorization':'Bearer '+self.device['credential']} if child else {'Cookie':'kidoo_session=test-parent'}
        if browser:headers['X-Kidoo-Browser']='1'
        if data is not None: headers['Content-Type']='application/json'
        conn.request(method,path,None if data is None else json.dumps(data),headers)
        response=conn.getresponse();status=response.status;body=response.read();conn.close()
        return status,json.loads(body)
    def test_jellyfin_connection_is_parent_only_and_preserves_mapping_on_failure(self):
        body={'profileId':'family','server':'https://jellyfin.example','username':'parent','password':'test-password'}
        mapping={'server':'https://jellyfin.example','userId':'user','token':'secret-token'}
        with patch('kidoo_server.jellyfin.authenticate',return_value=mapping) as auth, patch('kidoo_server.jellyfin.save_config') as save:
            self.assertIn(self.request('POST','/api/security/jellyfin-connect',body,browser=True)[0],(401,403))
            self.assertEqual(self.request('POST','/api/security/jellyfin-connect',body,child=False)[0],403)
            self.assertEqual(self.request('POST','/api/security/jellyfin-connect',dict(body,profileId='missing'),child=False,browser=True)[0],400)
            self.assertEqual(self.request('POST','/api/security/jellyfin-connect',dict(body,server='http://jellyfin.example'),child=False,browser=True)[0],400)
            self.assertEqual(self.request('POST','/api/security/jellyfin-connect',dict(body,server='HTTP://jellyfin.example'),child=False,browser=True)[0],400)
            auth.assert_not_called();save.assert_not_called()
            status,result=self.request('POST','/api/security/jellyfin-connect',body,child=False,browser=True)
            self.assertEqual((status,result),(200,{'ok':True}))
            save.assert_called_once_with(Path(self.server.db_path).parent,'family',mapping)
            save.reset_mock()
            status,_=self.request('POST','/api/security/jellyfin-connect',dict(body,server='http://jellyfin.example',allowInsecure=True),child=False,browser=True)
            self.assertEqual(status,200);save.assert_called_once();save.reset_mock()
            auth.side_effect=__import__('jellyfin').JellyfinError('Jellyfin refused access. Check the username, password and account permissions.')
            status,result=self.request('POST','/api/security/jellyfin-connect',body,child=False,browser=True)
            self.assertEqual(status,502);self.assertIn('Check the username',result['error']);save.assert_not_called()
            self.assertNotIn('test-password',json.dumps(result));self.assertNotIn('secret-token',json.dumps(result))
    def test_retired_endpoints_and_forged_operations_fail(self):
        for path in ('/api/tv/youtube/family','/api/discovery/family','/api/youtube/temp/family/1','/api/channel-review/family','/api/recommendations/family'):
            self.assertEqual(self.request('GET',path,child=False)[0],404,path)
        for path in ('/api/youtube/temp','/api/your-tv/prefetch','/api/channel-review/refresh','/api/recommendations/session'):
            self.assertEqual(self.request('POST',path,{'profileId':'family','videoId':1},child=False)[0],404,path)
        for path in ('/api/activity/start','/api/tv/favourite','/api/dashboard/review'):
            data={'profileId':'family','kind':'youtube','itemId':'1','favourite':True,'status':'reviewed'}
            self.assertGreaterEqual(self.request('POST',path,data,child=False)[0],400,path)
    def test_child_profile_approval_revocation_and_device_revocation(self):
        status,home=self.request('GET','/api/tv/home/family')
        self.assertEqual(status,200)
        self.assertEqual([v['id'] for v in home['local']],['allowed'])
        self.assertEqual(self.request('GET','/api/dashboard/family')[0],403)
        self.assertEqual(self.request('GET','/api/tv/home/other')[0],403)
        self.assertEqual(self.request('GET','/media/unknown')[0],403)
        self.server.security.set_approval('family','local','allowed',False)
        self.assertEqual(self.request('GET','/media/allowed')[0],403)
        self.assertEqual(self.request('GET','/api/tv/home/family')[1]['local'],[])
        self.server.security.revoke_device(self.device['deviceId'])
        self.assertEqual(self.request('GET','/api/tv/home/family')[0],401)
    def test_parent_metadata_groups_only_approved_episodes_and_enforces_limits(self):
        entries={'allowed':{'id':'allowed','title':'File one','path':self.media/'one'},
                 'private':{'id':'private','title':'File two','path':self.media/'two'}}
        self.index.stop();self.index=patch('kidoo_server.media_index',return_value=entries);self.index.start()
        metadata={'title':'First episode','summary':'A story','mediaType':'Episode','seriesName':'Example series','seasonNumber':1,'episodeNumber':1,'tags':['animals']}
        request={'itemId':'allowed','metadata':metadata}
        self.assertEqual(self.request('GET','/api/local/catalogue')[0],403)
        self.assertEqual(self.request('POST','/api/local/metadata',request)[0],403)
        self.assertEqual(self.request('POST','/api/local/metadata',request,child=False)[0],200)
        self.assertEqual(self.request('POST','/api/local/metadata',{'itemId':'private','metadata':dict(metadata,title='Hidden episode',episodeNumber=2)},child=False)[0],200)
        home=self.request('GET','/api/tv/home/family')[1]
        self.assertEqual(len(home['sections']['series']),1)
        self.assertEqual([v['title'] for v in home['sections']['series'][0]['episodes']],['First episode'])
        channels=self.request('GET','/api/your-tv/family')[1]['channels']
        self.assertIn('animals',[v['id'] for v in channels])
        limits={'total':None,'movies':None,'series':0,'other':None}
        self.assertEqual(self.request('POST','/api/viewing-limits',{'profileId':'family','limits':limits},child=False)[0],200)
        self.assertEqual(self.request('POST','/api/activity/start',{'profileId':'family','kind':'local','itemId':'allowed'})[0],429)
        invalid=dict(metadata,seasonNumber=-1)
        self.assertEqual(self.request('POST','/api/local/metadata',{'itemId':'allowed','metadata':invalid},child=False)[0],400)
    def test_local_recommendations_respect_approval_and_show_less(self):
        entries={'allowed':{'id':'allowed','title':'Animal','path':self.media/'one'},
                 'private':{'id':'private','title':'Hidden','path':self.media/'two'}}
        self.index.stop();self.index=patch('kidoo_server.media_index',return_value=entries);self.index.start()
        metadata={'title':'Animal film','summary':'','mediaType':'Movie','tags':['animals']}
        for ident in entries:
            self.assertEqual(self.request('POST','/api/local/metadata',{'itemId':ident,'metadata':metadata},child=False)[0],200)
        with sqlite3.connect(self.server.db_path) as db:
            db.execute('INSERT OR REPLACE INTO discovery_preferences VALUES (?,?)',('family',json.dumps({'interests':['animals']})))
        home=self.request('GET','/api/tv/home/family?suggestionSession=1')[1]
        recommended=next(row for row in home['rows'] if row['id']=='recommended')
        self.assertEqual([v['id'] for v in recommended['items']],['allowed'])
        change={'profileId':'family','kind':'local','itemId':'allowed','showLess':True}
        self.assertEqual(self.request('POST','/api/promotion/preferences',change)[0],403)
        self.assertEqual(self.request('POST','/api/promotion/preferences',change,child=False)[0],200)
        home=self.request('GET','/api/tv/home/family?suggestionSession=2')[1]
        self.assertNotIn('recommended',[row['id'] for row in home['rows']])
        self.assertEqual([v['id'] for v in home['sections']['movies']],['allowed'])
    def test_jellyfin_rating_policy_is_opt_in_and_enforced_for_child(self):
        mapping={'server':'https://jellyfin.example','userId':'user','token':'test'}
        self.mapping.stop();self.mapping=patch('kidoo_server.jellyfin.load_config',return_value={'family':mapping});self.mapping.start()
        movie={'Id':'movie','Name':'A movie','Type':'Movie','ProviderIds':{'Tmdb':'123'},'OfficialRating':'PG','UserData':{}}
        hidden={**movie,'Id':'hidden','OfficialRating':'R'}
        series={'Id':'show','Type':'Series','ProviderIds':{'Tvdb':'456'},'OfficialRating':'PG'}
        episode={'Id':'episode','Type':'Episode','SeriesId':'show','OfficialRating':'PG','UserData':{}}
        clip={'Id':'clip','Type':'Video','UserData':{}}
        raw={v['Id']:v for v in (movie,hidden,series,episode,clip)}
        def items(*args,**kwargs):
            entries=[]
            for v in (movie,hidden,episode,clip):
                entries.append({'id':'jellyfin:'+v['Id'],'title':v.get('Name',v['Id']),'type':v['Type'],'seriesId':v.get('SeriesId'),'series':'Show' if v.get('SeriesId') else None,'rating':v.get('OfficialRating'),'providerIds':v.get('ProviderIds',{}),'overview':'A story to review','hasArtwork':True,'progress':{'position':0,'completed':False},'duration':60})
            return {'items':entries,'total':len(entries)}
        policy={'mode':'automatic','system':'us','maximum':'PG','seriesFallback':False}
        with patch('kidoo_server.jellyfin.list_items',side_effect=items), patch('kidoo_server.jellyfin.list_series_artwork',return_value={}), patch('kidoo_server.jellyfin.get_item',side_effect=lambda m,i:raw[i]):
            self.assertIn(self.request('POST','/api/security/jellyfin-policy',{'profileId':'family','policy':policy,'confirmed':True})[0],(401,403))
            home=self.request('GET','/api/tv/home/family')[1];self.assertEqual(home['jellyfin'],[])
            preview=self.request('POST','/api/security/jellyfin-preview',{'profileId':'family','policy':policy},child=False)
            self.assertEqual(preview[0],200);self.assertEqual(sum(x['decision']['eligible'] for x in preview[1]['items']),2)
            self.assertEqual(preview[1]['items'][0]['overview'],'A story to review')
            self.assertTrue(preview[1]['items'][0]['thumbnail'].startswith('/api/jellyfin/artwork/family/'))
            self.assertEqual(preview[1]['jellyfinUrl'],'https://jellyfin.example/web/')
            self.assertEqual(self.request('POST','/api/security/jellyfin-policy',{'profileId':'family','policy':policy},child=False)[0],403)
            self.assertEqual(self.request('POST','/api/security/jellyfin-policy',{'profileId':'family','policy':policy,'confirmed':True},child=False)[0],200)
            home=self.request('GET','/api/tv/home/family')[1];self.assertEqual({v['id'] for v in home['jellyfin']},{'movie','episode'})
            self.assertEqual(self.request('POST','/api/jellyfin/start',{'profileId':'family','itemId':'hidden'})[0],403)
            with patch('kidoo_server.jellyfin.playback_info',return_value={'PlaySessionId':'session'}), patch('kidoo_server.jellyfin.report_playback'):
                status,started=self.request('POST','/api/jellyfin/start',{'profileId':'family','itemId':'movie'})
                self.assertEqual(status,200)
                self.assertEqual(self.request('POST','/api/security/jellyfin-override',{'profileId':'family','itemId':'movie','choice':'block'},child=False)[0],200)
                self.assertEqual(self.request('GET',started['streamUrl'])[0],403)
                self.assertEqual(self.request('POST','/api/security/jellyfin-override',{'profileId':'family','itemId':'movie','choice':'automatic'},child=False)[0],200)
            self.assertEqual(self.request('POST','/api/security/jellyfin-override',{'profileId':'family','itemId':'show','choice':'block'},child=False)[0],200)
            home=self.request('GET','/api/tv/home/family')[1];self.assertEqual({v['id'] for v in home['jellyfin']},{'movie'})
            self.assertEqual(self.request('POST','/api/jellyfin/start',{'profileId':'family','itemId':'episode'})[0],403)
            self.assertEqual(self.request('POST','/api/security/jellyfin-override',{'profileId':'family','itemId':'movie','choice':'block'},child=False)[0],200)
            self.assertEqual(self.request('POST','/api/jellyfin/start',{'profileId':'family','itemId':'movie'})[0],403)
            self.assertEqual(self.request('GET','/api/tv/home/family')[1]['jellyfin'],[])
            self.assertEqual(self.request('POST','/api/security/jellyfin-override',{'profileId':'family','itemId':'movie','choice':'allow'},child=False)[0],200)
            suggest={**policy,'mode':'suggest'}
            self.assertEqual(self.request('POST','/api/security/jellyfin-policy',{'profileId':'family','policy':suggest},child=False)[0],200)
            self.assertEqual({v['id'] for v in self.request('GET','/api/tv/home/family')[1]['jellyfin']},{'movie'})
            self.assertEqual(self.request('POST','/api/security/jellyfin-override',{'profileId':'family','itemId':'clip','choice':'allow'},child=False)[0],200)
            self.assertEqual({v['id'] for v in self.request('GET','/api/tv/home/family')[1]['jellyfin']},{'movie','clip'})

    def test_certificate_health_is_parent_only(self):
        self.server.tls_cert_path='test-certificate'
        with patch('kidoo_server.certificate_health.read',return_value={'configured':True,'expiresAt':1800000000,'expiringSoon':False,'expired':False}):
            self.assertEqual(self.request('GET','/api/security/certificate')[0],403)
            status,health=self.request('GET','/api/security/certificate',child=False)
            self.assertEqual(status,200)
            self.assertTrue(health['configured'])
            self.assertNotIn('path',health)
    def test_your_tv_rejects_online_sources(self):
        definitions=self.request('GET','/api/your-tv/manage/family',child=False)[1]['channels']
        self.assertTrue(definitions)
        self.assertTrue(all(set(v['sources']) <= {'local','jellyfin'} for v in definitions))
        invalid={'id':'test','name':'Test','topics':['all'],'sources':['youtube'],'languages':[],'enabled':True}
        self.assertEqual(self.request('POST','/api/your-tv/save',{'profileId':'family','channel':invalid},child=False)[0],400)
        with sqlite3.connect(self.server.db_path) as db:
            db.execute('INSERT INTO your_tv_definitions VALUES (?,?,?)',('family','test',json.dumps(invalid)))
        definitions=self.request('GET','/api/your-tv/manage/family',child=False)[1]['channels']
        legacy=next(v for v in definitions if v['id']=='test')
        self.assertFalse(legacy['enabled'])
        self.assertNotIn('youtube',legacy['sources'])
        with sqlite3.connect(self.server.db_path) as db:
            stored=json.loads(db.execute("SELECT definition FROM your_tv_definitions WHERE id='test'").fetchone()[0])
        self.assertEqual(stored['sources'],['youtube']) # The original legacy record is unchanged.

    def test_channel_queue_completion_and_daily_limit(self):
        status,listing=self.request('GET','/api/your-tv/family')
        self.assertEqual(status,200)
        channel=next(v for v in listing['channels'] if v['id']=='surprise')
        status,started=self.request('POST','/api/your-tv/start',{'profileId':'family','channelId':'surprise','offerId':channel['offerId']})
        self.assertEqual(status,200)
        self.assertEqual(started['item']['id'],'allowed')
        status,activity=self.request('POST','/api/activity/start',{'profileId':'family','kind':'local','itemId':'allowed','channelSession':started['sessionId']})
        self.assertEqual(status,200)
        event={'profileId':'family','sessionId':activity['sessionId'],'sequence':1,'watchedSeconds':10,'duration':10,'position':10,'finished':True}
        self.assertEqual(self.request('POST','/api/activity/event',event)[0],200)
        status,next_item=self.request('POST','/api/your-tv/next',{'profileId':'family','sessionId':started['sessionId']})
        self.assertEqual(status,200)
        self.assertEqual(next_item['item']['id'],'allowed') # Safe repeat when only one title remains.
        with sqlite3.connect(self.server.db_path) as db:
            db.execute('INSERT OR REPLACE INTO viewing_limits VALUES (?,?,?,?,?,?)',('family',0,None,None,None,None))
        self.assertEqual(self.request('POST','/api/activity/start',{'profileId':'family','kind':'local','itemId':'allowed'})[0],429)

    def test_limits_api_preserves_archived_column_without_exposing_it(self):
        with sqlite3.connect(self.server.db_path) as db:
            db.execute('INSERT INTO viewing_limits VALUES (?,?,?,?,?,?)',('family',120,90,None,25,None))
        status,limits=self.request('GET','/api/viewing-limits/family',child=False)
        self.assertEqual(status,200)
        self.assertNotIn('youtube',limits['limits'])
        new={'total':60,'movies':30,'series':None,'other':None}
        self.assertEqual(self.request('POST','/api/viewing-limits',{'profileId':'family','limits':new},child=False)[0],200)
        self.assertEqual(self.request('POST','/api/viewing-limits',{'profileId':'family','limits':dict(new,youtube=5)},child=False)[0],400)
        with sqlite3.connect(self.server.db_path) as db:
            self.assertEqual(db.execute("SELECT total,movies,youtube FROM viewing_limits WHERE profile_id='family'").fetchone(),(60,30,25))

    def test_two_devices_share_limit_and_duplicate_reports_do_not_add_time(self):
        limits={'total':1,'movies':None,'series':None,'other':None}
        self.assertEqual(self.request('POST','/api/viewing-limits',{'profileId':'family','limits':limits},child=False)[0],200)
        first=self.device
        second=self.server.security.redeem_pairing(self.server.security.create_pairing('family','Second device'))
        start={'profileId':'family','kind':'local','itemId':'allowed'}
        first_id=self.request('POST','/api/activity/start',start)[1]['sessionId']
        self.device=second
        second_id=self.request('POST','/api/activity/start',start)[1]['sessionId']
        event={'profileId':'family','sessionId':first_id,'sequence':1,'watchedSeconds':30,'duration':120,'position':30,'finished':False}
        self.device=first
        self.assertEqual(self.request('POST','/api/activity/event',event)[1]['limit']['remaining'],30)
        self.assertEqual(self.request('POST','/api/activity/event',event)[1]['limit']['remaining'],30)
        self.device=second
        event.update(sessionId=second_id)
        self.assertTrue(self.request('POST','/api/activity/event',event)[1]['limit']['blocked'])
        self.assertEqual(self.request('POST','/api/activity/start',start)[0],429)

    def test_jellyfin_session_resume_and_approval_revocation(self):
        mapping={'server':'fixture','userId':'test-user'}
        video={'Id':'movie1','Type':'Movie','UserData':{'PlaybackPositionTicks':420000000,'Played':False}}
        self.server.security.set_approval('family','jellyfin','movie1',True,'fixture')
        with patch('kidoo_server.jellyfin.load_config',return_value={'family':mapping}), \
             patch('kidoo_server.jellyfin.get_item',return_value=video), \
             patch('kidoo_server.jellyfin.playback_info',return_value={'PlaySessionId':'play-id'}), \
             patch('kidoo_server.jellyfin.report_playback') as report:
            status,started=self.request('POST','/api/jellyfin/start',{'profileId':'family','itemId':'movie1'})
            self.assertEqual(status,200)
            self.assertEqual(started['position'],42)
            report.assert_called_once()
            self.server.security.set_approval('family','jellyfin','movie1',False,'fixture')
            self.assertEqual(self.request('GET',started['streamUrl'])[0],403)
            self.assertEqual(self.request('POST','/api/jellyfin/start',{'profileId':'family','itemId':'movie1'})[0],403)

if __name__=='__main__': unittest.main()
