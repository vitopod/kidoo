#!/usr/bin/env python3
"""Local Kidoo media prototype. Use only on a trusted network."""
import argparse
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sqlite3
import ssl
import time
import threading
import math
import unicodedata
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote, parse_qs, urlencode
from tv_catalogue import INTERESTS, library_interest_signals, recommend_library, deprioritized_library
import jellyfin
import jellyfin_age_policy
import dashboard as parent_dashboard
import time_limits
import your_tv
import local_metadata
import certificate_health
from security import SecurityStore, SecurityError

ROOT = Path(__file__).resolve().parent
VIDEO_TYPES = {'.mp4', '.m4v', '.webm', '.mkv', '.mov'}
LANGUAGES = set()
DEFAULT_DISCOVERY = {'interests': ['space','making']}

def artwork_url(profile, item, kind='primary', tag=None):
    params = {}
    if kind == 'backdrop': params['type'] = 'backdrop'
    if isinstance(tag,str) and tag: params['v'] = tag
    return '/api/jellyfin/artwork/'+profile+'/'+item + ('?'+urlencode(params) if params else '')


def promotion_preferences(db, profile):
    return set(db.execute('SELECT kind,item_id FROM promotion_preferences WHERE profile_id=?',(profile,)))

def recent_suggestions(db, profile, device, visit, now):
    """Item keys offered on the three most recent other visits, never this visit."""
    visits=db.execute('''SELECT device_id,visit FROM recommendation_impressions
        WHERE profile_id=? AND offered_at>=? AND NOT (device_id=? AND visit=?)
        GROUP BY device_id,visit ORDER BY MAX(offered_at) DESC LIMIT 3''',
        (profile,now-30*86400,device,visit)).fetchall()
    return {row[0] for dev,number in visits for row in db.execute(
        'SELECT item_key FROM recommendation_impressions WHERE profile_id=? AND device_id=? AND visit=?',
        (profile,dev,number))}

def remember_suggestions(db, profile, device, visit, ids, now):
    """Keep only bounded, local offer history; a repeat request is idempotent."""
    db.execute('DELETE FROM recommendation_impressions WHERE profile_id=? AND offered_at<?',(profile,now-30*86400))
    db.executemany('INSERT OR IGNORE INTO recommendation_impressions VALUES (?,?,?,?,?)',
                   [(profile,device,visit,ident,now) for ident in ids])
    db.execute('''DELETE FROM recommendation_impressions WHERE rowid IN
        (SELECT rowid FROM recommendation_impressions WHERE profile_id=? ORDER BY offered_at DESC LIMIT -1 OFFSET 240)''',(profile,))



def init_db(path):
    with sqlite3.connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS progress (
          profile_id TEXT NOT NULL REFERENCES profiles(id), media_id TEXT NOT NULL,
          position REAL NOT NULL, duration REAL, completed INTEGER NOT NULL DEFAULT 0,
          updated_at REAL NOT NULL, PRIMARY KEY(profile_id, media_id));
        CREATE TABLE IF NOT EXISTS approvals (media_id TEXT PRIMARY KEY, approved INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS local_preferences (profile_id TEXT PRIMARY KEY, settings TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS local_metadata (media_id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS discovery_preferences (profile_id TEXT PRIMARY KEY, settings TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jellyfin_approvals (server TEXT NOT NULL, item_id TEXT NOT NULL, PRIMARY KEY(server, item_id));
        CREATE TABLE IF NOT EXISTS jellyfin_favourites (profile_id TEXT NOT NULL, server TEXT NOT NULL, item_id TEXT NOT NULL, PRIMARY KEY(profile_id,server,item_id));
        CREATE TABLE IF NOT EXISTS jellyfin_sessions (id TEXT PRIMARY KEY, profile TEXT NOT NULL, item TEXT NOT NULL, server TEXT NOT NULL, user_id TEXT NOT NULL, play_id TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 0, closed INTEGER NOT NULL DEFAULT 0, last_position REAL, last_played INTEGER);
        CREATE TABLE IF NOT EXISTS recommendation_epochs (profile_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS recommendation_reset_times (profile_id TEXT PRIMARY KEY, reset_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS recommendation_sessions (
          id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, video_id INTEGER NOT NULL,
          epoch INTEGER NOT NULL, watched_seconds REAL NOT NULL DEFAULT 0,
          duration REAL NOT NULL DEFAULT 0, started_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS recommendation_profile_epoch ON recommendation_sessions(profile_id,epoch,video_id);
        CREATE TABLE IF NOT EXISTS recommendation_impressions (
          profile_id TEXT NOT NULL, device_id TEXT NOT NULL, visit INTEGER NOT NULL,
          item_key TEXT NOT NULL, offered_at REAL NOT NULL,
          PRIMARY KEY(profile_id,device_id,visit,item_key));
        CREATE INDEX IF NOT EXISTS recommendation_impressions_recent ON recommendation_impressions(profile_id,offered_at);
        CREATE TABLE IF NOT EXISTS promotion_preferences (
          profile_id TEXT NOT NULL, kind TEXT NOT NULL, item_id TEXT NOT NULL,
          PRIMARY KEY(profile_id,kind,item_id));
        CREATE TABLE IF NOT EXISTS activity_sessions (
          id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, kind TEXT NOT NULL, item_id TEXT NOT NULL,
          started_at REAL NOT NULL, updated_at REAL NOT NULL, watched_seconds REAL NOT NULL DEFAULT 0,
          duration REAL NOT NULL DEFAULT 0, position REAL NOT NULL DEFAULT 0,
          finished INTEGER NOT NULL DEFAULT 0, sequence INTEGER NOT NULL DEFAULT 0,
          recommendation_session TEXT);
        CREATE INDEX IF NOT EXISTS activity_profile_time ON activity_sessions(profile_id,updated_at);
        CREATE UNIQUE INDEX IF NOT EXISTS activity_recommendation_session ON activity_sessions(recommendation_session) WHERE recommendation_session IS NOT NULL;
        CREATE TABLE IF NOT EXISTS activity_minutes (
          session_id TEXT NOT NULL, minute INTEGER NOT NULL, seconds REAL NOT NULL,
          PRIMARY KEY(session_id,minute));
        CREATE TABLE IF NOT EXISTS content_reviews (
          profile_id TEXT NOT NULL, kind TEXT NOT NULL, item_id TEXT NOT NULL,
          status TEXT NOT NULL, note TEXT NOT NULL, updated_at REAL NOT NULL,
          PRIMARY KEY(profile_id,kind,item_id));
        CREATE TABLE IF NOT EXISTS schema_flags (name TEXT PRIMARY KEY);
        ''')
        time_limits.init(db)
        your_tv.init(db)
        jellyfin_age_policy.init(db)
        columns={row[1] for row in db.execute('PRAGMA table_info(jellyfin_sessions)')}
        if 'last_position' not in columns: db.execute('ALTER TABLE jellyfin_sessions ADD COLUMN last_position REAL')
        if 'last_played' not in columns: db.execute('ALTER TABLE jellyfin_sessions ADD COLUMN last_played INTEGER')
        if 'transcode' not in columns: db.execute('ALTER TABLE jellyfin_sessions ADD COLUMN transcode INTEGER NOT NULL DEFAULT 0')
        # A prior recommendation reset must not revive historical Jellyfin signals.
        db.execute('INSERT OR IGNORE INTO recommendation_reset_times SELECT profile_id,? FROM recommendation_epochs WHERE epoch>0',(time.time(),))
        db.execute("INSERT OR IGNORE INTO profiles(id,name) VALUES ('family','Family')")

def media_index(root):
    entries = {}
    for path in sorted(root.rglob('*')):
        try:
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in VIDEO_TYPES:
                continue
            path.resolve().relative_to(root)
        except (OSError, ValueError):
            continue
        rel = path.relative_to(root).as_posix()
        key = hashlib.sha256(rel.encode()).hexdigest()[:24]
        entries[key] = {'id': key, 'title': path.stem.replace('_', ' '), 'path': path, 'relativePath': rel}
    return entries

class KidooServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, media_dir, db_path, password):
        self.media_dir = Path(media_dir).resolve()
        self.db_path = str(db_path)
        self.password = password
        self.sessions = {}
        self.protected_mode = False
        self.tls_cert_path = None
        self.jellyfin_lock = threading.RLock()
        self.jellyfin_hls = {}
        self.jellyfin_metadata_cache = {}
        self.jellyfin_metadata_lock = threading.RLock()
        self.security_lock = threading.RLock()
        init_db(self.db_path)
        self.cache_policy_lock=threading.RLock()
        self.security = SecurityStore(self.db_path)
        super().__init__(address, Handler)

class Handler(BaseHTTPRequestHandler):
    server_version = 'Kidoo/0.1'
    def log_message(self, format, *args):
        # Avoid logging URLs or user data.
        return
    def send_json(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def body(self):
        length = int(self.headers.get('Content-Length', '0'))
        if length < 0 or length > 16384: raise ValueError('Invalid request size')
        obj = json.loads(self.rfile.read(length))
        if not isinstance(obj, dict): raise ValueError('Expected an object')
        return obj
    def child_device(self):
        """Only accept paired credentials on a directly encrypted connection."""
        credential=self.child_credential()
        if not self.secure_transport() or not credential: return None
        return self.server.security.device(credential)
    def cookie_value(self, name):
        try:
            cookies=SimpleCookie();cookies.load(self.headers.get('Cookie',''))
            return cookies[name].value if cookies.get(name) else ''
        except Exception: return ''
    def child_credential(self):
        authorization=self.headers.get('Authorization','')
        if authorization.startswith('Bearer '): return authorization[7:]
        return self.cookie_value('__Host-kidoo_child') if self.secure_transport() else ''
    def has_child_auth(self):
        return self.headers.get('Authorization','').startswith('Bearer ') or bool(self.cookie_value('__Host-kidoo_child'))
    def parent_allowed(self):
        auth=self.headers.get('Authorization','')
        if auth.startswith('Bearer '):
            return self.secure_transport() and self.server.security.is_unlocked(auth[7:],self.headers.get('X-Kidoo-Parent-Session',''))
        if self.cookie_value('__Host-kidoo_child'):
            return self.secure_transport() and self.server.security.is_unlocked(self.child_credential(),self.cookie_value('__Host-kidoo_parent'))
        if not self.server.password and not self.server.protected_mode: return True
        try:
            cookies=SimpleCookie(); cookies.load(self.headers.get('Cookie',''))
            token=cookies['kidoo_session'].value if cookies.get('kidoo_session') else ''
            with self.server.security_lock:
                if self.server.sessions.get(token,0)<=time.time():
                    self.server.sessions.pop(token,None);return False
                self.server.sessions[token]=time.time()+300
                return True
        except Exception: return False
    def allowed(self):
        if self.has_child_auth(): return bool(self.child_device())
        return self.parent_allowed()
    def secure_transport(self):
        # Never trust forwarded headers: the direct connection must use TLS.
        return isinstance(self.connection, ssl.SSLSocket)
    def child_approval_ids(self, profile, kind, namespace=''):
        with sqlite3.connect(self.server.db_path) as db:
            approved={r[0] for r in db.execute('SELECT item_id FROM security_profile_approvals WHERE profile_id=? AND kind=? AND namespace=?',(profile,kind,namespace))}
            policy=jellyfin_age_policy.load(db,profile) if kind=='jellyfin' else None
            series_override=bool(db.execute('SELECT 1 FROM jellyfin_series_overrides WHERE profile_id=? AND namespace=? LIMIT 1',(profile,namespace)).fetchone()) if policy else False
        if not policy or (policy['mode']!='automatic' and not series_override): return approved
        try:
            mapping=jellyfin.load_config(Path(self.server.db_path).parent).get(profile)
            if not mapping or mapping['server']!=namespace: return set()
            result=set();start=0
            while True:
                page=jellyfin.list_items(mapping,start,1000);items=page['items']
                for item in items:
                    ident=item['id'].split(':',1)[1]
                    raw={'Id':ident,'Type':item.get('type'),'SeriesId':item.get('seriesId'),
                         'OfficialRating':item.get('rating'),'ProviderIds':item.get('providerIds')}
                    if self.jellyfin_allowed(profile,ident,mapping,raw): result.add(ident)
                start+=len(items)
                if not items or start>=page['total']: break
            return result
        except (jellyfin.JellyfinError,ValueError,KeyError,TypeError): return set()

    def jellyfin_metadata(self, mapping, item):
        if not isinstance(item,str) or not item.isalnum():raise jellyfin.JellyfinError('Invalid metadata identity')
        key=(mapping['server'],mapping['userId'],item);now=time.monotonic()
        with self.server.jellyfin_metadata_lock:
            cached=self.server.jellyfin_metadata_cache.get(key)
            if cached and cached[0]>now:return cached[1]
        value=jellyfin.get_item(mapping,item)
        if not isinstance(value,dict) or value.get('Id')!=item: raise jellyfin.JellyfinError('Invalid metadata')
        with self.server.jellyfin_metadata_lock:
            if len(self.server.jellyfin_metadata_cache)>3000:self.server.jellyfin_metadata_cache.clear()
            self.server.jellyfin_metadata_cache[key]=(now+30,value)
        return value

    def jellyfin_allowed(self, profile, item, mapping, raw=None):
        with sqlite3.connect(self.server.db_path) as db:
            policy=jellyfin_age_policy.load(db,profile)
            blocks={r[0] for r in db.execute("SELECT item_id FROM security_profile_blocks WHERE profile_id=? AND kind='jellyfin' AND namespace=?",(profile,mapping['server']))}
            allowed={r[0] for r in db.execute("SELECT item_id FROM security_profile_approvals WHERE profile_id=? AND kind='jellyfin' AND namespace=?",(profile,mapping['server']))}
        if item in blocks:return False
        if policy['mode']!='automatic' and item in allowed and not blocks:return True
        try:
            raw=raw or self.jellyfin_metadata(mapping,item)
            series_id=raw.get('SeriesId') if raw.get('Type')=='Episode' else None
            if raw.get('Type')=='Episode' and not series_id and blocks:return False
            if series_id and series_id in blocks:return False
            if item in allowed or (series_id and series_id in allowed):return True
            if policy['mode']!='automatic':return False
            series=self.jellyfin_metadata(mapping,series_id) if series_id else None
            return jellyfin_age_policy.evaluate(raw,policy,series)['eligible']
        except (jellyfin.JellyfinError,ValueError,TypeError):return False
    def child_block_ids(self, profile, kind):
        with sqlite3.connect(self.server.db_path) as db:
            return {r[0] for r in db.execute('SELECT item_id FROM security_profile_blocks WHERE profile_id=? AND kind=?',(profile,kind))}
    def child_local_allowed(self, profile, item):
        if not self.server.security.is_approved(profile,'local',item): return False
        with sqlite3.connect(self.server.db_path) as db:
            row=db.execute('SELECT settings FROM local_preferences WHERE profile_id=?',(profile,)).fetchone()
        settings=json.loads(row[0]) if row else {'dismissedIds':[]}
        return item not in settings.get('dismissedIds',[])
    def child_get_allowed(self, path, device):
        profile=device['profileId'];parts=path.split('/')
        query=parse_qs(urlsplit(self.path).query)
        if query.get('view',['family'])[0]=='parent': return False
        if path in ('/api/profiles', '/api/your-tv/'+profile, '/api/your-tv/status/'+profile): return True
        if path == '/api/tv/home/'+profile: return True
        if len(parts)==6 and parts[1:4]==['api','viewing-limits','status'] and parts[4]==profile: return True
        if len(parts)==5 and parts[1:4]==['api','tv','artwork']:
            return self.child_local_allowed(profile,parts[4])
        if len(parts)==3 and parts[1]=='media':
            return self.child_local_allowed(profile,parts[2])
        if len(parts)==6 and parts[1:4]==['api','jellyfin','artwork'] and parts[4]==profile:
            try:
                mapping=jellyfin.load_config(Path(self.server.db_path).parent).get(profile)
                if not mapping: return False
                if self.jellyfin_allowed(profile,parts[5],mapping): return True
                approved=self.child_approval_ids(profile,'jellyfin',mapping['server'])
                return jellyfin.series_has_approved_episode(mapping,parts[5],approved)
            except (jellyfin.JellyfinError,ValueError): return False
        if len(parts) in (5,6) and parts[1:4]==['api','jellyfin','stream']:
            session=self.jellyfin_session(parts[4])

            if not session or session['profile']!=profile:return False
            try:
                mapping=jellyfin.load_config(Path(self.server.db_path).parent).get(profile)
                return bool(mapping and mapping['server']==session['server'] and self.jellyfin_allowed(profile,session['item'],mapping))
            except (jellyfin.JellyfinError,ValueError):return False
        return False
    def child_post_allowed(self, path, data, device):
        if data.get('profileId') != device['profileId']: return False
        if path in ('/api/your-tv/start','/api/your-tv/next','/api/tv/favourite','/api/activity/start','/api/activity/event','/api/jellyfin/start',
                    '/api/jellyfin/event','/api/progress'): return True
        if path=='/api/tv/local-preferences':
            new=data.get('settings')
            if not isinstance(new,dict): return False
            with sqlite3.connect(self.server.db_path) as db:
                row=db.execute('SELECT settings FROM local_preferences WHERE profile_id=?',(device['profileId'],)).fetchone()
            old=json.loads(row[0]) if row else {'favouriteIds':[],'dismissedIds':[]}
            return new.get('dismissedIds')==old.get('dismissedIds')
        return False
    def is_same_origin(self):
        origin = self.headers.get('Origin')
        if not origin: return True
        parsed=urlsplit(origin)
        return parsed.scheme==('https' if self.secure_transport() else 'http') and parsed.netloc == self.headers.get('Host')
    def jellyfin_session(self, ident):
        with sqlite3.connect(self.server.db_path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM jellyfin_sessions WHERE id=?', (ident,)).fetchone()
            return dict(row) if row else None
    def limit_stream_allowed(self, profile, kind, item, category):
        """Limited playback requires a recently reporting, profile-bound session."""
        child=getattr(self,'_request_child',None)
        token=parse_qs(urlsplit(self.path).query).get('activity',[''])[0]
        with sqlite3.connect(self.server.db_path) as db:
            quota=time_limits.status(db,profile,category)
            if quota['blocked']: return False
            row=db.execute('''SELECT timer_device,timer_seen,content_category FROM activity_sessions
                WHERE id=? AND profile_id=? AND kind=? AND item_id=?''',(token,profile,kind,str(item))).fetchone()
            if row and row[2]!=category: return False
            if not quota['enabled']: return True
            return bool(row and row[2]==category and time.time()-row[1]<20 and (not child or self.parent_allowed() or row[0]==child['id']))
    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path in ('/', '/app.js', '/style.css', '/child', '/child.js', '/child.css', '/kidoo-logo.jpg', '/kidoo-logo-refined.png', '/dashboard', '/dashboard.js', '/dashboard.css'):
            file = ROOT / 'home' / ('index.html' if path == '/' else 'dashboard.html' if path=='/dashboard' else 'child.html' if path=='/child' else path[1:])
            return self.file(file)
        if path == '/api/tv/info':
            return self.send_json({'product':'Kidoo','apiVersion':1,'nativeHome':True,'localPlayback':True,'jellyfin':True,'secureParentRoles':self.server.protected_mode,'sharedPassword':not self.server.protected_mode})
        if path == '/api/session':
            browser=bool(self.cookie_value('__Host-kidoo_child'))
            device=self.child_device() if browser else None
            return self.send_json({'authenticated': self.allowed(),'protectedMode':self.server.protected_mode,'browserChild':browser,'profileId':device['profileId'] if device else None,'parentUnlocked':bool(browser and device and self.parent_allowed())})
        if path == '/api/security/certificate':
            if not self.secure_transport() or not self.parent_allowed(): return self.send_json({'error':'Parent sign-in required'},403)
            return self.send_json(certificate_health.read(self.server.tls_cert_path))
        if path.startswith('/api/security/jellyfin-policy/'):
            if not self.secure_transport() or not self.parent_allowed(): return self.send_json({'error':'Parent sign-in required'},403)
            if not self.server.protected_mode:return self.send_json({'error':'Protected mode required'},403)
            profile=path.split('/')[-1]
            with sqlite3.connect(self.server.db_path) as db:
                if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone():return self.send_json({'error':'Unknown profile'},404)
                return self.send_json({'policy':jellyfin_age_policy.load(db,profile),'systems':jellyfin_age_policy.RATINGS})
        if path.startswith('/api/security/approvals/'):
            if not self.secure_transport(): return self.send_json({'error':'HTTPS required'},403)
            if not (self.server.password or self.server.protected_mode) or not self.parent_allowed(): return self.send_json({'error':'Parent sign-in required'},401)
            profile=path.split('/')[-1]
            with sqlite3.connect(self.server.db_path) as db:
                if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone(): return self.send_json({'error':'Unknown profile'},404)
                rows=db.execute("SELECT kind,item_id,namespace FROM security_profile_approvals WHERE profile_id=? AND kind IN ('local','jellyfin')",(profile,)).fetchall()
                blocks=db.execute("SELECT kind,item_id,namespace FROM security_profile_blocks WHERE profile_id=? AND kind IN ('local','jellyfin')",(profile,)).fetchall()
            return self.send_json({'approvals':[{'kind':r[0],'itemId':r[1],'namespace':r[2]} for r in rows], 'blocks':[{'kind':r[0],'itemId':r[1]} for r in blocks]})
        if path == '/api/security/devices':
            if not self.secure_transport(): return self.send_json({'error':'HTTPS required'}, 403)
            if not (self.server.password or self.server.protected_mode) or not self.parent_allowed(): return self.send_json({'error':'Parent sign-in required'}, 401)
            return self.send_json({'devices':self.server.security.devices()})
        bearer=self.has_child_auth()
        child=self.child_device() if bearer else None
        if (bearer and not child) or (not bearer and not self.parent_allowed()): return self.send_json({'error':'Sign in required'},401)
        self._request_child=child
        if child and not self.parent_allowed() and not self.child_get_allowed(path,child): return self.send_json({'error':'Access denied'},403)
        if path.startswith('/api/your-tv/manage/'):
            if not self.parent_allowed(): return self.send_json({'error':'Parent sign-in required'},403)
            profile=path.split('/')[-1]
            with sqlite3.connect(self.server.db_path) as db:
                if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone():return self.send_json({'error':'Unknown profile'},404)
                return self.send_json({'channels':your_tv.defaults(db,profile)})
        if path.startswith('/api/your-tv/status/'):
            profile=path.split('/')[-1]
            return self.send_json({'channels':self.playlist_status(profile)})
        if path.startswith('/api/your-tv/'):
            profile=path.split('/')[-1]
            with sqlite3.connect(self.server.db_path) as db:
                if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone():return self.send_json({'error':'Unknown profile'},404)
                definitions=your_tv.defaults(db,profile)
            candidates=self.your_tv_candidates(profile)
            channels=[];used=set();device=child['id'] if child else 'parent'
            with sqlite3.connect(self.server.db_path) as db:
                db.execute('BEGIN IMMEDIATE')
                interests,recent=your_tv.viewing_signals(db,profile,candidates)
                for definition in definitions:
                    if not definition['enabled']: continue
                    items=self.reconcile_channel(db,profile,definition,candidates,interests,list(used)+recent)
                    if items:
                        first=items[0]
                        if not first:continue
                        used.add(first['key'])
                        token=your_tv.offer(db,profile,device,definition['id'],first['key'])
                        score=sum(interests.get(topic.casefold(),0) for topic in definition['topics'] if topic.casefold()!='all')
                        channels.append({'id':definition['id'],'name':definition['name'],'count':len(items),'offerId':token,
                            'artworkKey':your_tv.artwork_key(definition),'firstTitle':first.get('title','Video'),
                            'firstDuration':your_tv.duration_seconds(first),'_score':score})
            channels.sort(key=lambda c:(c['id']!='surprise',-c['_score'],c['name'].casefold()))
            statuses={v['id']:v for v in self.playlist_status(profile)}
            for channel in channels:
                channel.pop('_score');channel.update(statuses.get(channel['id'],{}))
            return self.send_json({'channels':channels})
        if path.startswith('/api/viewing-limits/status/'):
            parts=path.split('/')
            if len(parts)!=6 or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}',parts[5]): return self.send_json({'error':'Invalid session'},400)
            profile,session=parts[4],parts[5]
            with sqlite3.connect(self.server.db_path) as db:
                row=db.execute('SELECT content_category,timer_device FROM activity_sessions WHERE id=? AND profile_id=?',(session,profile)).fetchone()
                if not row:return self.send_json({'error':'Unknown viewing session'},404)
                if child and not self.parent_allowed() and row[1]!=child['id']:return self.send_json({'error':'Access denied'},403)
                return self.send_json(time_limits.status(db,profile,row[0]))
        if path.startswith('/api/viewing-limits/'):
            if not self.parent_allowed(): return self.send_json({'error':'Parent sign-in required'},403)
            profile=path.split('/')[-1]
            with sqlite3.connect(self.server.db_path) as db:
                if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone(): return self.send_json({'error':'Unknown profile'},404)
                return self.send_json(time_limits.status(db,profile))
        if path.startswith('/api/promotion/preferences/'):
            if not self.parent_allowed(): return self.send_json({'error':'Parent sign-in required'},403)
            profile=path.split('/')[-1]
            with sqlite3.connect(self.server.db_path) as db:
                if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone(): return self.send_json({'error':'Unknown profile'},404)
                items=promotion_preferences(db,profile)
            return self.send_json({'items':[{'kind':kind,'itemId':ident} for kind,ident in sorted(items)]})
        if path.startswith('/api/dashboard/'):
            profile=path.split('/')[-1]
            try:
                days=int(parse_qs(urlsplit(self.path).query).get('days',['30'])[0])
                if days not in (0,7,30,90): raise ValueError()
            except ValueError: return self.send_json({'error':'Invalid period'},400)
            with sqlite3.connect(self.server.db_path) as db:
                if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone(): return self.send_json({'error':'Unknown profile'},404)
            mapping_error=False
            try: mapping=jellyfin.load_config(Path(self.server.db_path).parent).get(profile)
            except jellyfin.JellyfinError: mapping=None; mapping_error=True
            result=parent_dashboard.dashboard(self.server.db_path,profile,days,media_index(self.server.media_dir),[],[],mapping)
            if mapping_error: result['coverage']['jellyfinError']=True
            return self.send_json(result)
        if path.startswith('/api/tv/artwork/'):
            item=media_index(self.server.media_dir).get(path.split('/')[-1])
            if item:
                for suffix in ('.jpg','.png','.jpeg'):
                    image=item['path'].with_suffix(suffix)
                    if image.is_file() and not image.is_symlink() and self.server.media_dir in image.resolve().parents: return self.file(image)
            return self.send_json({'error':'Artwork unavailable'},404)
        if path.startswith('/api/tv/home/'):
            return self.native_home(path.split('/')[-1])
        if path.startswith('/api/jellyfin/artwork/'):
            parts = path.split('/')
            if len(parts) != 6 or not parts[5].isalnum(): return self.send_json({'error':'Artwork unavailable'},404)
            profile, item = parts[4], parts[5]
            with sqlite3.connect(self.server.db_path) as db:
                if not db.execute('SELECT 1 FROM profiles WHERE id=?', (profile,)).fetchone(): return self.send_json({'error':'Unknown profile'},404)
            try:
                mapping = jellyfin.load_config(Path(self.server.db_path).parent).get(profile)
                if not mapping or jellyfin.get_item(mapping,item).get('Id') != item: return self.send_json({'error':'Artwork unavailable'},404)
                image_type = parse_qs(urlsplit(self.path).query).get('type',['primary'])[0]
                if image_type not in ('primary','backdrop'): return self.send_json({'error':'Artwork unavailable'},404)
                content_type, content = jellyfin.artwork(mapping,item,'Backdrop' if image_type=='backdrop' else 'Primary')
                self.send_response(200); self.send_header('Content-Type',content_type); self.send_header('Content-Length',str(len(content))); self.send_header('Cache-Control','private, max-age=300'); self.end_headers(); self.wfile.write(content)
                return
            except (jellyfin.JellyfinError, ValueError): return self.send_json({'error':'Artwork unavailable'},404)
        if path.startswith('/api/jellyfin/catalogue/'):
            profile = path.split('/')[-1]
            with sqlite3.connect(self.server.db_path) as db:
                if not db.execute('SELECT 1 FROM profiles WHERE id=?', (profile,)).fetchone():
                    return self.send_json({'error': 'Unknown profile'}, 404)
            try:
                params = parse_qs(urlsplit(self.path).query)
                start, limit = int(params.get('start', ['0'])[0]), int(params.get('limit', ['50'])[0])
                search = params.get('search', [''])[0].strip()
                if start < 0 or not 1 <= limit <= 100 or len(search) > 100: raise ValueError()
                mapping = jellyfin.load_config(Path(self.server.db_path).parent).get(profile)
                if not mapping: return self.send_json({'error': 'Profile is not connected to Jellyfin'}, 404)
                result = jellyfin.list_items(mapping, start, limit, search=search) if search else jellyfin.list_items(mapping, start, limit)
                with sqlite3.connect(self.server.db_path) as db:
                    approved = {row[0] for row in db.execute('SELECT item_id FROM jellyfin_approvals WHERE server=?', (mapping['server'],))}
                if child or self.server.protected_mode: approved=self.child_approval_ids(profile,'jellyfin',mapping['server'])
                for item in result['items']: item['approved'] = item['id'][len('jellyfin:'):] in approved
                return self.send_json(result)
            except ValueError:
                return self.send_json({'error': 'Invalid pagination'}, 400)
            except jellyfin.JellyfinError:
                return self.send_json({'error': 'Jellyfin is unavailable or its connection needs attention'}, 502)
        if path.startswith('/api/jellyfin/stream/'):
            parts=path.split('/')
            if len(parts) not in (5,6): return self.send_json({'error':'Invalid stream'},404)
            ident=parts[4]
            with self.server.jellyfin_lock:
                session = self.jellyfin_session(ident)
                if not session or session['closed']:
                    return self.send_json({'error': 'Playback session expired'}, 404)
                try:
                    mapping = jellyfin.load_config(Path(self.server.db_path).parent).get(session['profile'])
                    if not mapping or mapping['server'] != session['server'] or mapping['userId'] != session['user_id']:
                        return self.send_json({'error': 'Profile mapping changed'}, 403)
                    with sqlite3.connect(self.server.db_path) as db:
                        approved = db.execute('SELECT 1 FROM jellyfin_approvals WHERE server=? AND item_id=?', (mapping['server'], session['item'])).fetchone()
                    if self.server.protected_mode or (child and not self.parent_allowed()): approved=self.jellyfin_allowed(session['profile'],session['item'],mapping)
                    if not approved: return self.send_json({'error': 'Video is not approved'}, 403)
                    if not self.limit_stream_allowed(session['profile'],'jellyfin',session['item'],session.get('content_category') or 'other'):return self.send_json({'error':'Viewing time or playback authorization has expired'},429)
                    hls=self.server.jellyfin_hls.get(ident)
                    if session['transcode'] and not hls: return self.send_json({'error':'Playback session expired'},404)
                    if len(parts)==6 and not hls: return self.send_json({'error':'Playback session expired'},404)
                    resource=(hls['resources'].get(parts[5]) if len(parts)==6 else hls['master']) if hls else None
                    if hls and not resource: return self.send_json({'error':'Unknown stream resource'},404)
                    stream = jellyfin.open_transcode(mapping,resource) if hls else jellyfin.open_stream(mapping, session['item'], self.headers.get('Range'))
                except (jellyfin.JellyfinError,ValueError):
                    return self.send_json({'error': 'Jellyfin stream unavailable'}, 502)
            try:
                with stream:
                    if hls and resource.split('?',1)[0].lower().endswith('.m3u8'):
                        data=stream.read(1024*1024+1)
                        if len(data)>1024*1024: return self.send_json({'error':'Playlist too large'},502)
                        lines=[]
                        for line in data.decode('utf-8').splitlines():
                            if line.startswith('#') and 'URI=' in line: return self.send_json({'error':'Unsupported playlist'},502)
                            if line and not line.startswith('#'):
                                safe=jellyfin.safe_transcode_path(line,session['item'],resource)
                                key=hashlib.sha256(safe.encode()).hexdigest()[:24]
                                with self.server.jellyfin_lock:
                                    if len(hls['resources'])>=4096 and key not in hls['resources']: return self.send_json({'error':'Playlist too large'},502)
                                    hls['resources'][key]=safe
                                activity=parse_qs(urlsplit(self.path).query).get('activity',[''])[0]
                                line='/api/jellyfin/stream/'+ident+'/'+key+('?' + urlencode({'activity':activity}) if activity else '')
                            lines.append(line)
                        body=('\n'.join(lines)+'\n').encode()
                        self.send_response(200);self.send_header('Content-Type','application/vnd.apple.mpegurl');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body);return
                    self.send_response(stream.status)
                    for header in ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges'):
                        if stream.headers.get(header): self.send_header(header, stream.headers[header])
                    self.send_header('Cache-Control', 'no-store'); self.end_headers()
                    while True:
                        if child and not (self.child_device() and (self.parent_allowed() or self.jellyfin_allowed(session['profile'],session['item'],mapping))): break
                        chunk = stream.read(65536)
                        if not chunk: break
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError, ssl.SSLEOFError): pass
            except (ValueError,UnicodeError): return self.send_json({'error':'Invalid transcoding playlist'},502)
            return
        if path == '/api/profiles':
            with sqlite3.connect(self.server.db_path) as db:
                child=getattr(self,'_request_child',None)
                rows=db.execute('SELECT id,name FROM profiles WHERE id=?',(child['profileId'],)) if child and not self.parent_allowed() else db.execute('SELECT id,name FROM profiles ORDER BY name')
                return self.send_json([{'id':x[0],'name':x[1]} for x in rows])
        if path == '/api/local/catalogue':
            if not self.parent_allowed(): return self.send_json({'error':'Parent sign-in required'},403)
            with sqlite3.connect(self.server.db_path) as db: metadata=local_metadata.load(db)
            return self.send_json({'items':[{'id':v['id'],'filename':v.get('relativePath',v['title']),'metadata':metadata.get(v['id'],{})} for v in media_index(self.server.media_dir).values()]})
        if path.startswith('/api/library/'):
            profile = path.split('/')[-1]
            with sqlite3.connect(self.server.db_path) as db:
                if not db.execute('SELECT 1 FROM profiles WHERE id=?', (profile,)).fetchone(): return self.send_json({'error':'Unknown profile'}, 404)
                progress = {row[0]: {'position': row[1], 'duration': row[2], 'completed': bool(row[3]), 'updatedAt': row[4]} for row in db.execute('SELECT media_id,position,duration,completed,updated_at FROM progress WHERE profile_id=?', (profile,))}
                approved = {row[0]: bool(row[1]) for row in db.execute('SELECT media_id,approved FROM approvals')}
            if self.server.protected_mode: approved={x:True for x in self.child_approval_ids(profile,'local')}
            return self.send_json([{'id': item['id'], 'title': item['title'], 'approved': approved.get(item['id'], False), 'progress': progress.get(item['id'])} for item in media_index(self.server.media_dir).values()])
        if path.startswith('/media/'):
            item = media_index(self.server.media_dir).get(path.split('/')[-1])
            if item:
                profile=child['profileId'] if child and not self.parent_allowed() else parse_qs(urlsplit(self.path).query).get('profileId',[''])[0]
                with sqlite3.connect(self.server.db_path) as db: metadata=local_metadata.load(db).get(item['id'],{})
                category=time_limits.category('local',metadata.get('mediaType'))
                if profile and not self.limit_stream_allowed(profile,'local',item['id'],category):return self.send_json({'error':'Viewing time or playback authorization has expired'},429)
                check=(lambda: bool(self.child_device()) and (self.parent_allowed() or self.child_local_allowed(child['profileId'],item['id']))) if child else None
                return self.file(item['path'], ranged=True,check=check)
        return self.send_json({'error':'Not found'}, 404)

    def your_tv_candidates(self,profile):
        home=self.native_home(profile,as_data=True)
        with sqlite3.connect(self.server.db_path) as db: preferences=promotion_preferences(db,profile)
        return [item for item in home['local']+home['jellyfin']
                if item.get('approved') and not deprioritized_library(item,preferences)]


    def reconcile_channel(self,db,profile,definition,candidates,interests,recent):
        return your_tv.reconcile_queue(db,profile,definition,candidates,interests,recent)


    def playlist_status(self,profile):
        with sqlite3.connect(self.server.db_path) as db:
            rows=db.execute('SELECT channel_id,item_key FROM your_tv_queue WHERE profile_id=? ORDER BY channel_id,position',(profile,)).fetchall()
        result={}
        for channel,key in rows:
            if not key.startswith(('local:','jellyfin:')): continue
            entry=result.setdefault(channel,{'id':channel,'readyCount':0,'totalCount':0,'cacheProblem':False})
            entry['totalCount']+=1;entry['readyCount']+=1
        return list(result.values())




    def native_home(self, profile, as_data=False):
        query=parse_qs(urlsplit(self.path).query); family=as_data or query.get('view',['family'])[0]!='parent'
        variation=query.get('suggestionSession',[''])[0]
        if variation and (not variation.isascii() or not variation.isdigit() or len(variation)>10):
            return self.send_json({'error':'Invalid suggestion session'},400)
        variation=int(variation) if variation else None
        with sqlite3.connect(self.server.db_path) as db:
            if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone(): return self.send_json({'error':'Unknown profile'},404)
            row=db.execute('SELECT settings FROM discovery_preferences WHERE profile_id=?',(profile,)).fetchone()
            settings=json.loads(row[0]) if row else dict(DEFAULT_DISCOVERY)
            row=db.execute('SELECT settings FROM local_preferences WHERE profile_id=?',(profile,)).fetchone()
            local_settings=json.loads(row[0]) if row else {'favouriteIds':[],'dismissedIds':[]}
            local_approved={r[0]:bool(r[1]) for r in db.execute('SELECT media_id,approved FROM approvals')}
            local_details=local_metadata.load(db)
            progress={r[0]:{'position':r[1],'duration':r[2],'completed':bool(r[3]),'updatedAt':r[4]} for r in db.execute('SELECT media_id,position,duration,completed,updated_at FROM progress WHERE profile_id=?',(profile,))}
            reset=db.execute('SELECT reset_at FROM recommendation_reset_times WHERE profile_id=?',(profile,)).fetchone()
            since=reset[0] if reset else 0
            library_history={(r[0],r[1]):{'seconds':r[2], 'duration':r[3]} for r in db.execute("SELECT kind,item_id,SUM(watched_seconds),MAX(duration) FROM activity_sessions WHERE profile_id=? AND kind IN ('local','jellyfin') AND started_at>=? GROUP BY kind,item_id",(profile,since))}
            show_less=promotion_preferences(db,profile) if family else set()
            recent_sources={}
            independent_items=set(db.execute('SELECT kind,item_id FROM your_tv_preserved WHERE profile_id=?',(profile,)))
            for kind,item,channel in db.execute("SELECT kind,item_id,your_tv_channel FROM activity_sessions WHERE profile_id=? AND (your_tv_channel='' OR watched_seconds>0) ORDER BY started_at DESC,rowid DESC",(profile,)):
                recent_sources.setdefault((kind,item),channel)
                if not channel: independent_items.add((kind,item))
            channel_latest={(kind,item):bool(finished) for kind,item,finished in db.execute('''SELECT l.kind,l.item_id,s.finished FROM your_tv_latest l JOIN activity_sessions s ON s.id=l.activity_id WHERE l.profile_id=?''',(profile,))}
        child=getattr(self,'_request_child',None)
        if child or self.server.protected_mode:
            local_approved={x:True for x in self.child_approval_ids(profile,'local')}
        local=[]
        for v in media_index(self.server.media_dir).values():
            hidden=v['id'] in local_settings['dismissedIds']; ok=local_approved.get(v['id'],False)
            if family and (not ok or hidden):continue
            meta=local_details.get(v['id'],{})
            name=meta.get('seriesName','')
            local.append({'id':v['id'],'key':'local:'+v['id'],'kind':'local','title':meta.get('title') or v['title'],'summary':meta.get('summary',''),
                'mediaType':meta.get('mediaType','Other'),'seriesName':name,'seriesId':'local-'+hashlib.sha256(name.casefold().encode()).hexdigest()[:24] if name else '',
                'seasonNumber':meta.get('seasonNumber'),'episodeNumber':meta.get('episodeNumber'),'tags':meta.get('tags',[]),
                'creator':name or 'Family library','approved':ok,'favourite':v['id'] in local_settings['favouriteIds'],'hidden':hidden,'thumbnail':'/api/tv/artwork/'+v['id'],'progress':progress.get(v['id']),'url':'/media/'+v['id']})
        jellyfin_items=[]; jellyfin_resume=[]; jellyfin_total=0; jellyfin_error=False; series_artwork={}
        try:
            mapping = jellyfin.load_config(Path(self.server.db_path).parent).get(profile)
            if mapping:
                with sqlite3.connect(self.server.db_path) as db:
                    jellyfin_approved={r[0] for r in db.execute('SELECT item_id FROM jellyfin_approvals WHERE server=?',(mapping['server'],))}
                    jellyfin_favourites={r[0] for r in db.execute('SELECT item_id FROM jellyfin_favourites WHERE profile_id=? AND server=?',(profile,mapping['server']))}
                if child or self.server.protected_mode: jellyfin_approved=self.child_approval_ids(profile,'jellyfin',mapping['server'])
                if not as_data:
                    try: series_artwork=jellyfin.list_series_artwork(mapping)
                    except jellyfin.JellyfinError: pass
                def convert_jellyfin(v):
                    item=v['id'].split(':',1)[1]; p=v['progress']; date=p.get('updatedAt')
                    try: updated=datetime.fromisoformat(date.replace('Z','+00:00')).timestamp() if date else 0
                    except ValueError: updated=0
                    return {'id':item,'key':'jellyfin:'+item,'kind':'jellyfin','title':v['title'],'summary':v.get('overview') or '',
                        'creator':v.get('series') or 'Jellyfin','approved':item in jellyfin_approved,'favourite':item in jellyfin_favourites,
                        'mediaType':v.get('type'), 'seriesId':v.get('seriesId'), 'seriesName':v.get('series'), 'genres':v.get('genres') or [],
                        'seasonNumber':v.get('seasonNumber'), 'episodeNumber':v.get('episodeNumber'),
                        'thumbnail':artwork_url(profile,item,tag=v.get('artworkTag')) if v.get('hasArtwork') else '',
                        'heroImage':artwork_url(profile,item,'backdrop',v.get('backdropTag')) if v.get('hasBackdrop') else '',
                        'artworkAspectRatio':v.get('artworkAspectRatio'),
                        'progress':{'position':p['position'],'duration':v['duration'],'completed':p['completed'],'updatedAt':updated},'url':''}
                result=jellyfin.list_items(mapping,0,1000); jellyfin_total=result['total']
                items=list(result['items'])
                while len(items)<jellyfin_total:
                    batch=jellyfin.list_items(mapping,len(items),1000)['items']
                    if not batch: break
                    items.extend(batch)
                jellyfin_items=[convert_jellyfin(v) for v in items]
                jellyfin_resume=[] if as_data else [convert_jellyfin(v) for v in jellyfin.list_items(mapping,0,100,resume=True)['items']]
                if family:
                    jellyfin_items=[v for v in jellyfin_items if v['approved']]
                    jellyfin_resume=[v for v in jellyfin_resume if v['approved']]
                    if child and not self.parent_allowed(): jellyfin_total=len(jellyfin_items)
        except (jellyfin.JellyfinError, ValueError, TypeError): jellyfin_error=True
        if as_data:
            return {'local':local,'jellyfin':jellyfin_items}
        movies=[v for v in local+jellyfin_items if v.get('mediaType')=='Movie']
        other=[v for v in local+jellyfin_items if v.get('mediaType') not in ('Movie','Episode')]
        shows={}
        for item in local+jellyfin_items:
            if item.get('mediaType')!='Episode': continue
            name=item.get('seriesName') or 'Other episodes'
            identity=item.get('seriesId') or hashlib.sha256(name.casefold().encode()).hexdigest()[:24]
            key='series:'+identity
            if key not in shows:
                art=series_artwork.get(item.get('seriesId'),{})
                cover=artwork_url(profile,identity,tag=art.get('artworkTag')) if item['kind']=='jellyfin' and art.get('hasArtwork') else ''
                backdrop=artwork_url(profile,identity,'backdrop',art.get('backdropTag')) if art.get('hasBackdrop') else ''
                shows[key]={'id':identity,'key':key,'kind':'series','title':name,'creator':'Series',
                    'summary':'Choose an episode to watch.','approved':True,'favourite':False,
                    'thumbnail':cover or backdrop or item['thumbnail'],'heroImage':backdrop,
                    'artworkAspectRatio':art.get('artworkAspectRatio') if cover else 16/9 if backdrop else item.get('artworkAspectRatio'),
                    'progress':None,'episodes':[]}
            shows[key]['episodes'].append(item)
        for show in shows.values():
            show['episodes'].sort(key=lambda x:(x.get('seasonNumber') if isinstance(x.get('seasonNumber'),int) else 1000000,
                x.get('episodeNumber') if isinstance(x.get('episodeNumber'),int) else 1000000,x['title'].casefold()))
        shows_list=sorted(shows.values(),key=lambda x:x['title'].casefold())
        if family:
            movies.sort(key=lambda v:deprioritized_library(v,show_less))
            shows_list.sort(key=lambda v:deprioritized_library(v,show_less))
        resume=[]
        for item in local+jellyfin_resume:
            if not item['progress'] or item['progress']['completed'] or item['progress']['position']<=0: continue
            source_kind=item.get('kind')
            source_key=(source_kind,str(item['id']))
            channel_origin=bool(recent_sources.get(source_key))
            if channel_origin and source_key not in independent_items and (source_key not in channel_latest or channel_latest[source_key]):continue
            if item.get('mediaType')=='Episode' and not channel_origin:
                name=item.get('seriesName') or 'Other episodes'
                identity=item.get('seriesId') or hashlib.sha256(name.casefold().encode()).hexdigest()[:24]
                show=shows.get('series:'+identity)
                if not show: continue
                item={**show,'progress':item['progress']}
            if not any(x['key']==item['key'] for x in resume): resume.append(item)
        try:current=int(query.get('current',['0'])[0])
        except ValueError:current=0
        rows=[]
        def row(key,title,items):
            if items:rows.append({'id':key,'title':title,'items':items})
        row('continue','Continue watching',sorted(resume,key=lambda v:-v['progress']['updatedAt']))
        signals=library_interest_signals([v for v in local+jellyfin_items if not deprioritized_library(v,show_less)],library_history)
        track=family and variation is not None and not current
        device=child['id'] if child else 'legacy'
        with sqlite3.connect(self.server.db_path) as db:
            recent=recent_suggestions(db,profile,device,variation,time.time()) if track else None
        recommendation_shows=[{**v,'episodes':[e for e in v['episodes'] if not deprioritized_library(e,show_less)]}
                              for v in shows_list if not deprioritized_library(v,show_less)
                              and (not any((e.get('progress') or {}).get('position',0)>0 or (e.get('progress') or {}).get('completed') for e in v['episodes']) or any(e.get('favourite') for e in v['episodes']))]
        recommended_library=recommend_library([v for v in local+jellyfin_items if not deprioritized_library(v,show_less)],
            recommendation_shows,settings,set(),signals,limit=24 if track else 4)
        full_shows={v['key']:v for v in shows_list}
        recommended_library=[{**full_shows[v['key']],'reason':v['reason']} if v.get('kind')=='series' else v for v in recommended_library]
        if track:
            recommended_library=sorted(recommended_library,key=lambda v:v['key'] in recent)[:4]
            with sqlite3.connect(self.server.db_path) as db:
                remember_suggestions(db,profile,device,variation,
                    [v['key'] for v in recommended_library],time.time())
        mixed=recommended_library
        row('recommended','Recommended for you',mixed)
        row('favourites','Your favourites',[v for v in local+jellyfin_items if v['favourite']])
        row('local','From your family',local)
        row('jellyfin','From Jellyfin',jellyfin_items)
        row('watched','Watch again',sorted([v for v in local if v['progress'] and v['progress']['completed']],key=lambda v:-v['progress']['updatedAt']))
        # Home previews never bypass the profile's eligibility or promote a show-less item.
        seen={v['key'] for v in resume+mixed}
        def preview(items):
            choices=[v for v in items if v['key'] not in seen]
            if choices and variation is not None:
                offset=variation % len(choices); choices=choices[offset:]+choices[:offset]
            chosen=choices[:12];seen.update(v['key'] for v in chosen);return chosen
        def unstarted(v):
            p=v.get('progress') or {}
            return not p.get('position') and not p.get('completed')
        home_shelves=[]
        def shelf(key,title,items):
            items=preview(items)
            if items:home_shelves.append({'id':key,'title':title,'items':items})
        shelf('movies','Movies to explore',[v for v in movies if not deprioritized_library(v,show_less) and unstarted(v)])
        shelf('series','Series to explore',[full_shows[v['key']] for v in recommendation_shows if v['episodes']])
        shelf('other','More videos',[v for v in other if not deprioritized_library(v,show_less) and unstarted(v)])
        result={'profileId':profile,'view':'family' if family else 'parent','updatedAt':time.time(),'rows':rows,
            'homeShelves':home_shelves,'settings':settings,'localSettings':local_settings,'languages':sorted(LANGUAGES),'interests':[{'id':i,'label':l} for i,l in INTERESTS],
            'local':local,'jellyfin':jellyfin_items,'jellyfinTotal':jellyfin_total,'jellyfinError':jellyfin_error,
            'sections':{'movies':movies,'series':shows_list,'other':other},
            'empty':{'local':not bool(local),'approved':not bool(local or jellyfin_items)},
            'limits':{'parentViewSecure':self.server.protected_mode}}
        return result if as_data else self.send_json(result)

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self.is_same_origin(): return self.send_json({'error':'Origin denied'}, 403)
        try:
            data = self.body()
            browser_cookie=bool(self.cookie_value('__Host-kidoo_child'))
            if (browser_cookie or path.startswith('/api/browser/')) and self.headers.get('X-Kidoo-Browser')!='1':
                return self.send_json({'error':'Browser request denied'},403)
            if path in ('/api/browser/pair','/api/browser/unlock','/api/browser/lock'):
                if not self.server.protected_mode or not self.secure_transport(): return self.send_json({'error':'Protected HTTPS required'},403)
                try:
                    if path=='/api/browser/pair':
                        paired=self.server.security.redeem_pairing(data.get('code'))
                        old_parent=self.cookie_value('kidoo_session')
                        with self.server.security_lock: self.server.sessions.pop(old_parent,None)
                        old_credential=self.child_credential(); old_unlock=self.cookie_value('__Host-kidoo_parent')
                        if old_credential and old_unlock: self.server.security.lock(old_credential,old_unlock)
                        payload=json.dumps({'ok':True,'profileId':paired['profileId']}).encode()
                        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store')
                        self.send_header('Set-Cookie','kidoo_session=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Strict')
                        self.send_header('Set-Cookie','__Host-kidoo_parent=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Strict')
                        self.send_header('Set-Cookie',f"__Host-kidoo_child={paired['credential']}; Max-Age=31536000; Path=/; Secure; HttpOnly; SameSite=Strict")
                        self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload);return
                    credential=self.cookie_value('__Host-kidoo_child')
                    if not credential or not self.server.security.device(credential): raise SecurityError('Device not paired')
                    if path=='/api/browser/unlock':
                        token=self.server.security.unlock(credential,data.get('pin'))
                        payload=b'{"ok":true}'
                        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store')
                        self.send_header('Set-Cookie',f'__Host-kidoo_parent={token}; Path=/; Secure; HttpOnly; SameSite=Strict')
                        self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload);return
                    self.server.security.lock(credential,self.cookie_value('__Host-kidoo_parent'))
                    payload=b'{"ok":true}'
                    self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store')
                    self.send_header('Set-Cookie','__Host-kidoo_parent=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Strict')
                    self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload);return
                except SecurityError:
                    return self.send_json({'error':'Pairing or parent unlock was not accepted'},403)
            if path == '/api/login':
                if self.server.protected_mode: return self.send_json({'error':'Use administrator PIN sign-in'},403)
                if not self.server.password or not secrets.compare_digest(str(data.get('password', '')), self.server.password): return self.send_json({'error':'Incorrect password'}, 401)
                token = secrets.token_urlsafe(32)
                with self.server.security_lock: self.server.sessions[token]=time.time()+300
                payload = b'{"ok":true}'
                self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Cache-Control','no-store')
                self.send_header('Set-Cookie', f'kidoo_session={token}; HttpOnly; SameSite=Strict; Path=/' + ('; Secure' if self.secure_transport() else ''))
                self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload); return
            if path.startswith('/api/security/'):
                # Security credentials must never travel over cleartext, including
                # behind an unverified forwarding header or local HTTP proxy.
                if not self.secure_transport(): return self.send_json({'error':'HTTPS required'},403)
                if not (self.server.password or self.server.protected_mode): return self.send_json({'error':'Parent sign-in required'},401)
                sec=self.server.security
                try:
                    if path in ('/api/security/pairing','/api/security/pin','/api/security/revoke','/api/security/approval','/api/security/import-approvals','/api/security/assign','/api/security/jellyfin-policy','/api/security/jellyfin-preview','/api/security/jellyfin-override','/api/security/jellyfin-connect'):
                        if not self.parent_allowed(): return self.send_json({'error':'Parent sign-in required'},401)
                        if path=='/api/security/jellyfin-connect':
                            if not self.server.protected_mode:return self.send_json({'error':'Protected mode required'},403)
                            if self.headers.get('X-Kidoo-Browser')!='1':return self.send_json({'error':'Open the parent dashboard to connect Jellyfin.'},403)
                            profile,server,username,password=(data.get(k) for k in ('profileId','server','username','password'))
                            if not all(isinstance(v,str) for v in (profile,server,username,password)) or not username.strip() or len(server)>2048 or len(username)>256 or len(password)>4096:
                                return self.send_json({'error':'Enter a Jellyfin address and username.'},400)
                            with sqlite3.connect(self.server.db_path) as db:
                                if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone():return self.send_json({'error':'Select an existing profile.'},400)
                            try:server=jellyfin.valid_url(server.strip())
                            except ValueError:return self.send_json({'error':'Enter a full Jellyfin address starting with http:// or https://, without a username or password in the address.'},400)
                            if urlsplit(server).scheme=='http' and data.get('allowInsecure') is not True:
                                return self.send_json({'error':'This Jellyfin address uses HTTP. Use HTTPS, or confirm that you accept the unencrypted connection to Jellyfin.'},400)
                            try:
                                mapping=jellyfin.authenticate(server,username.strip(),password)
                                jellyfin.save_config(Path(self.server.db_path).parent,profile,mapping)
                            except jellyfin.JellyfinError as error:return self.send_json({'error':str(error)},502)
                            except (ValueError,OSError):return self.send_json({'error':'The connection could not be saved. Check the Kidoo data folder permissions and try again.'},500)
                            return self.send_json({'ok':True})
                        if path in ('/api/security/jellyfin-policy','/api/security/jellyfin-preview','/api/security/jellyfin-override'):
                            if not self.server.protected_mode:return self.send_json({'error':'Protected mode required'},403)
                            profile=data.get('profileId')
                            if not isinstance(profile,str): raise SecurityError('Invalid profile')
                            with sqlite3.connect(self.server.db_path) as db:
                                if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone():raise SecurityError('Unknown profile')
                            mapping=jellyfin.load_config(Path(self.server.db_path).parent).get(profile)
                            if not mapping:return self.send_json({'error':'Connect this profile to Jellyfin first'},404)
                            if path=='/api/security/jellyfin-override':
                                ident,choice=data.get('itemId'),data.get('choice')
                                if not isinstance(ident,str) or not ident.isalnum() or choice not in ('allow','block','automatic'):raise SecurityError('Invalid override')
                                item=jellyfin.get_item(mapping,ident)
                                if not isinstance(item,dict) or item.get('Id')!=ident or item.get('Type') not in ('Movie','Episode','Series','Video'):raise SecurityError('Unknown item')
                                with sqlite3.connect(self.server.db_path) as db:
                                    series_args=(profile,mapping['server'],ident)
                                    args=(profile,'jellyfin',mapping['server'],ident)
                                    db.execute('DELETE FROM security_profile_approvals WHERE profile_id=? AND kind=? AND namespace=? AND item_id=?',args)
                                    db.execute('DELETE FROM security_profile_blocks WHERE profile_id=? AND kind=? AND namespace=? AND item_id=?',args)
                                    if choice=='allow':db.execute('INSERT INTO security_profile_approvals VALUES (?,?,?,?)',args)
                                    elif choice=='block':db.execute('INSERT INTO security_profile_blocks VALUES (?,?,?,?)',args)
                                    if item.get('Type')=='Series' and choice!='automatic':db.execute('INSERT OR IGNORE INTO jellyfin_series_overrides VALUES (?,?,?)',series_args)
                                    else:db.execute('DELETE FROM jellyfin_series_overrides WHERE profile_id=? AND namespace=? AND item_id=?',series_args)
                                return self.send_json({'ok':True})
                            try: policy=jellyfin_age_policy.validate(data.get('policy'))
                            except ValueError:raise SecurityError('Invalid policy') from None
                            if path=='/api/security/jellyfin-policy':
                                if policy['mode']=='automatic' and data.get('confirmed') is not True:raise SecurityError('Confirmation required')
                                with sqlite3.connect(self.server.db_path) as db:jellyfin_age_policy.save(db,profile,policy)
                                return self.send_json({'ok':True,'policy':policy})
                            results=[];start=0
                            # Preview must reflect edits just made in Jellyfin, even if
                            # the child playback metadata cache is still warm.
                            with self.server.jellyfin_metadata_lock:
                                self.server.jellyfin_metadata_cache={k:v for k,v in self.server.jellyfin_metadata_cache.items() if k[:2]!=(mapping['server'],mapping['userId'])}
                            while True:
                                page=jellyfin.list_items(mapping,start,1000);items=page['items']
                                for v in items:
                                    ident=v['id'].split(':',1)[1]
                                    raw={'Id':ident,'Type':v.get('type'),'SeriesId':v.get('seriesId'),'OfficialRating':v.get('rating'),'ProviderIds':v.get('providerIds')}
                                    try: series=self.jellyfin_metadata(mapping,raw['SeriesId']) if raw.get('Type')=='Episode' and raw.get('SeriesId') else None
                                    except (jellyfin.JellyfinError,ValueError):series=None
                                    decision=jellyfin_age_policy.evaluate(raw,policy,series)
                                    results.append({'id':ident,'title':v.get('title',''),'seriesId':v.get('seriesId'),'series':v.get('series'),
                                        'overview':v.get('overview') or '', 'year':v.get('year'), 'rating':v.get('rating'),
                                        'thumbnail':artwork_url(profile,ident,tag=v.get('artworkTag')) if v.get('hasArtwork') else '',
                                        'decision':decision})
                                start+=len(items)
                                if not items or start>=page['total']:break
                            return self.send_json({'items':results,'jellyfinUrl':jellyfin.valid_url(mapping['server'])+'/web/'})
                        if path=='/api/security/pairing':
                            return self.send_json({'code':sec.create_pairing(data.get('profileId'),data.get('label'))})
                        if path=='/api/security/pin':
                            with self.server.security_lock:
                                sec.set_pin(data.get('pin')); self.server.sessions.clear()
                            return self.send_json({'ok':True})
                        if path=='/api/security/import-approvals':
                            profile=data.get('profileId')
                            if not isinstance(profile,str): raise SecurityError('Invalid profile')
                            mapping=jellyfin.load_config(Path(self.server.db_path).parent).get(profile)
                            sec.import_legacy_approvals(profile,mapping['server'] if mapping else None)
                            return self.send_json({'ok':True})
                        if path=='/api/security/assign':
                            sec.assign_device(data.get('deviceId'),data.get('profileId'));return self.send_json({'ok':True})
                        if path=='/api/security/approval':
                            profile,kind,item,approved=data.get('profileId'),data.get('kind'),data.get('itemId'),data.get('approved')
                            if not isinstance(item,str) or not isinstance(profile,str) or not isinstance(approved,bool): raise SecurityError('Invalid approval')
                            namespace=''
                            if kind=='local':
                                if item not in media_index(self.server.media_dir): raise SecurityError('Unknown video')
                            elif kind=='jellyfin':
                                mapping=jellyfin.load_config(Path(self.server.db_path).parent).get(profile)
                                if not mapping or not item.isalnum() or jellyfin.get_item(mapping,item).get('Id')!=item: raise SecurityError('Unknown video')
                                namespace=mapping['server']
                            else: raise SecurityError('Invalid approval')
                            sec.set_approval(profile,kind,item,approved,namespace);return self.send_json({'ok':True})
                        device_id=data.get('deviceId')
                        if not isinstance(device_id,str) or len(device_id)>128: raise SecurityError('Invalid device')
                        sec.revoke_device(device_id); return self.send_json({'ok':True})
                    if path=='/api/security/admin-login':
                        with self.server.security_lock:
                            sec.authenticate_administrator(data.get('name'),data.get('pin'))
                            token=secrets.token_urlsafe(32);self.server.sessions[token]=time.time()+300
                        payload=b'{"ok":true}'
                        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store')
                        self.send_header('Set-Cookie',f'kidoo_session={token}; HttpOnly; SameSite=Strict; Path=/; Secure')
                        self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload);return
                    if path=='/api/security/redeem': return self.send_json(sec.redeem_pairing(data.get('code')))
                    if path=='/api/security/unlock':
                        with self.server.security_lock: session=sec.unlock(data.get('credential'),data.get('pin'))
                        return self.send_json({'session':session})
                    if path=='/api/security/lock':
                        sec.lock(data.get('credential'),data.get('session')); return self.send_json({'ok':True})
                    return self.send_json({'error':'Not found'},404)
                except (SecurityError,jellyfin.JellyfinError):
                    return self.send_json({'error':'Security request denied'},403)
            bearer=self.has_child_auth()
            child=self.child_device() if bearer else None
            if (bearer and not child) or (not bearer and not self.parent_allowed()): return self.send_json({'error':'Sign in required'},401)
            self._request_child=child
            if child and not self.parent_allowed() and not self.child_post_allowed(path,data,child): return self.send_json({'error':'Access denied'},403)
            if path == '/api/local/metadata':
                if not self.parent_allowed(): return self.send_json({'error':'Parent sign-in required'},403)
                ident=data.get('itemId')
                if not isinstance(ident,str) or ident not in media_index(self.server.media_dir): return self.send_json({'error':'Unknown video'},404)
                metadata=local_metadata.validate(data.get('metadata'))
                with sqlite3.connect(self.server.db_path) as db:
                    db.execute('INSERT OR REPLACE INTO local_metadata VALUES (?,?)',(ident,json.dumps(metadata,ensure_ascii=False)))
                return self.send_json({'ok':True})
            if path == '/api/your-tv/save':
                if not self.parent_allowed():return self.send_json({'error':'Parent sign-in required'},403)
                profile=data.get('profileId'); definition=your_tv.validate(data.get('channel'))
                if not isinstance(profile,str):raise ValueError('Invalid profile')
                with sqlite3.connect(self.server.db_path) as db:
                    if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone():return self.send_json({'error':'Unknown profile'},404)
                    your_tv.defaults(db,profile)
                    if db.execute('SELECT COUNT(*) FROM your_tv_definitions WHERE profile_id=?',(profile,)).fetchone()[0]>=30 and not db.execute('SELECT 1 FROM your_tv_definitions WHERE profile_id=? AND id=?',(profile,definition['id'])).fetchone():raise ValueError('Too many channels')
                    db.execute('INSERT OR REPLACE INTO your_tv_definitions VALUES (?,?,?)',(profile,definition['id'],json.dumps(definition)))
                return self.send_json({'ok':True})
            if path == '/api/your-tv/delete':
                if not self.parent_allowed():return self.send_json({'error':'Parent sign-in required'},403)
                profile,channel=data.get('profileId'),data.get('channelId')
                if not isinstance(profile,str) or not isinstance(channel,str):raise ValueError('Invalid channel')
                with sqlite3.connect(self.server.db_path) as db:
                    if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone():return self.send_json({'error':'Unknown profile'},404)
                    your_tv.defaults(db,profile);db.execute('DELETE FROM your_tv_definitions WHERE profile_id=? AND id=?',(profile,channel))
                return self.send_json({'ok':True})
            if path == '/api/your-tv/reset':
                if not self.parent_allowed():return self.send_json({'error':'Parent sign-in required'},403)
                profile=data.get('profileId')
                with sqlite3.connect(self.server.db_path) as db:
                    if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone():return self.send_json({'error':'Unknown profile'},404)
                    db.execute('DELETE FROM your_tv_definitions WHERE profile_id=?',(profile,));db.execute('DELETE FROM your_tv_initialized WHERE profile_id=?',(profile,));your_tv.defaults(db,profile)
                return self.send_json({'ok':True})
            if path in ('/api/your-tv/start','/api/your-tv/next'):
                profile=data.get('profileId'); child=getattr(self,'_request_child',None);device=child['id'] if child else 'parent'
                if not isinstance(profile,str):raise ValueError('Invalid profile')
                with sqlite3.connect(self.server.db_path) as check_db:
                    if not check_db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone():return self.send_json({'error':'Unknown profile'},404)
                candidates=self.your_tv_candidates(profile)
                with sqlite3.connect(self.server.db_path) as db:
                    db.execute('BEGIN IMMEDIATE')
                    definitions=your_tv.defaults(db,profile)
                    if path.endswith('/start'):
                        channel=data.get('channelId')
                        if not isinstance(channel,str):raise ValueError('Invalid channel')
                        offered=data.get('offerId')
                        if offered is not None and (not isinstance(offered,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}',offered)):raise ValueError('Invalid channel offer')
                        offered_key=your_tv.offered_item(db,offered,profile,device,channel) if offered else None
                        if offered and not offered_key:return self.send_json({'error':'Channel selection expired','refresh':True},409)
                        session=your_tv.start(db,profile,device,channel);history=[]
                    else:
                        session=data.get('sessionId')
                        if not isinstance(session,str):raise ValueError('Invalid session')
                        channel,history,_=your_tv.get_session(db,session,profile,device)
                    definition=next((d for d in definitions if d['id']==channel and d['enabled']),None)
                    if not definition:raise ValueError('Channel unavailable')
                    request_id=data.get('requestId','')
                    if not isinstance(request_id,str) or len(request_id)>64 or (request_id and not re.fullmatch(r'[A-Za-z0-9_-]+',request_id)):raise ValueError('Invalid request ID')
                    previous,current,preview_key=db.execute('SELECT last_request,current_key,preview_key FROM your_tv_sessions WHERE id=?',(session,)).fetchone()
                    interests,recent=your_tv.viewing_signals(db,profile,candidates)
                    playlist=self.reconcile_channel(db,profile,definition,candidates,interests,recent)
                    if request_id and previous==request_id:
                        item=next((v for v in candidates if v['key']==current and your_tv.matches(definition,v)),None)
                    else:
                        keys=[v['key'] for v in playlist]
                        if path.endswith('/start'):
                            item=playlist[0] if playlist else None
                        elif playlist:
                            index=(keys.index(current)+1)%len(keys) if current in keys else 0
                            item=playlist[index]
                        else:item=None
                        if path.endswith('/start') and offered and (not item or item['key']!=offered_key):
                            db.rollback();return self.send_json({'error':'Channel selection changed','refresh':True},409)
                        if item:history=(history+[item['key']])[-100:]
                        if item and playlist:
                            keys=[v['key'] for v in playlist];preview=playlist[(keys.index(item['key'])+1)%len(playlist)]
                        else:preview=None
                        preview_key=preview['key'] if preview else ''
                        db.execute('UPDATE your_tv_sessions SET history=?,current_key=?,last_request=?,preview_key=? WHERE id=?',
                                   (json.dumps(history),item['key'] if item else '',request_id,preview_key,session))
                    preview=next((v for v in candidates if v['key']==preview_key and your_tv.matches(definition,v)),None)
                return self.send_json({'sessionId':session,'channel':definition['name'],'item':item,'next':{'title':preview.get('title','Video')} if preview else None})
            if path == '/api/viewing-limits':
                if not self.parent_allowed(): return self.send_json({'error':'Parent sign-in required'},403)
                profile=data.get('profileId');limits=time_limits.validate(data.get('limits'))
                if not isinstance(profile,str): raise ValueError('Invalid profile')
                with sqlite3.connect(self.server.db_path) as db:
                    if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone(): return self.send_json({'error':'Unknown profile'},404)
                    db.execute('''INSERT INTO viewing_limits(profile_id,total,movies,series,other) VALUES (?,?,?,?,?)
                        ON CONFLICT(profile_id) DO UPDATE SET total=excluded.total,movies=excluded.movies,
                        series=excluded.series,other=excluded.other''',
                        (profile,*(limits[key] for key in time_limits.PUBLIC_FIELDS)))
                return self.send_json({'ok':True})
            if path == '/api/promotion/preferences':
                if not self.parent_allowed(): return self.send_json({'error':'Parent sign-in required'},403)
                profile,kind,ident,less=data.get('profileId'),data.get('kind'),data.get('itemId'),data.get('showLess')
                if (not isinstance(profile,str) or kind not in ('local','jellyfin','series')
                    or not isinstance(ident,str) or not ident or len(ident)>128 or not isinstance(less,bool)):
                    raise ValueError('Invalid promotion preference')
                if not ident.isascii() or not re.fullmatch(r'[A-Za-z0-9-]+',ident): raise ValueError('Invalid library item')
                if kind=='local' and ident not in media_index(self.server.media_dir): return self.send_json({'error':'Unknown video'},404)
                with sqlite3.connect(self.server.db_path) as db:
                    if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone(): return self.send_json({'error':'Unknown profile'},404)
                    if less: db.execute('INSERT OR IGNORE INTO promotion_preferences VALUES (?,?,?)',(profile,kind,ident))
                    else: db.execute('DELETE FROM promotion_preferences WHERE profile_id=? AND kind=? AND item_id=?',(profile,kind,ident))
                return self.send_json({'ok':True})
            if path == '/api/dashboard/review':
                profile,kind,ident=data.get('profileId'),data.get('kind'),data.get('itemId')
                status,note=data.get('status'),data.get('note','')
                if not isinstance(profile,str) or kind not in ('jellyfin','local') or not isinstance(ident,str) or status not in ('unreviewed','reviewed','discuss','avoid') or not isinstance(note,str) or len(note)>1000: raise ValueError('Invalid review')
                with sqlite3.connect(self.server.db_path) as db:
                    if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone(): return self.send_json({'error':'Unknown profile'},404)
                if kind=='local' and ident not in media_index(self.server.media_dir): return self.send_json({'error':'Unknown video'},404)
                if kind=='jellyfin':
                    try:
                        mapping=jellyfin.load_config(Path(self.server.db_path).parent).get(profile)
                        if not mapping or not ident.isalnum() or jellyfin.get_item(mapping,ident).get('Id')!=ident: return self.send_json({'error':'Unknown video'},404)
                    except (jellyfin.JellyfinError,ValueError): return self.send_json({'error':'Video unavailable'},502)
                with sqlite3.connect(self.server.db_path) as db:
                    db.execute('''INSERT INTO content_reviews VALUES (?,?,?,?,?,?) ON CONFLICT(profile_id,kind,item_id)
                                  DO UPDATE SET status=excluded.status,note=excluded.note,updated_at=excluded.updated_at''',(profile,kind,ident,status,note.strip(),time.time()))
                return self.send_json({'ok':True})
            if path == '/api/activity/start':
                profile,kind,item=data.get('profileId'),data.get('kind'),data.get('itemId')
                if not isinstance(profile,str) or kind not in ('jellyfin','local') or not isinstance(item,str): raise ValueError('Invalid activity')
                with sqlite3.connect(self.server.db_path) as db:
                    if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone(): return self.send_json({'error':'Unknown profile'},404)
                if kind=='local':
                    if child and not self.parent_allowed() and not self.child_local_allowed(profile,item): return self.send_json({'error':'Video not approved'},403)
                    if item not in media_index(self.server.media_dir): return self.send_json({'error':'Unknown video'},404)
                else:
                    linked=self.jellyfin_session(data.get('playbackSession',''))
                    if not linked or linked['profile']!=profile or linked['item']!=item or linked['closed']:
                        return self.send_json({'error':'Unknown playback session'},404)
                if kind=='local':
                    with sqlite3.connect(self.server.db_path) as db: metadata=local_metadata.load(db).get(item,{})
                category=linked.get('content_category') if kind=='jellyfin' else time_limits.category(kind,metadata.get('mediaType'))
                if category not in time_limits.CATEGORIES:
                    # Sessions created before upgrade are classified from Jellyfin itself.
                    mapping=jellyfin.load_config(Path(self.server.db_path).parent).get(profile)
                    if not mapping:return self.send_json({'error':'Unable to classify this video'},503)
                    category=time_limits.category(kind,jellyfin.get_item(mapping,item).get('Type'))
                    if category is None:return self.send_json({'error':'Unable to classify this video'},503)
                channel_candidate=None
                if data.get('channelSession') is not None:
                    channel_candidate=next((v for v in self.your_tv_candidates(profile) if v['key']==kind+':'+item),None)
                    if not channel_candidate:return self.send_json({'error':'Channel video no longer eligible'},403)
                rec=None
                with self.server.cache_policy_lock, sqlite3.connect(self.server.db_path) as db:
                    db.execute('BEGIN IMMEDIATE')
                    quota=time_limits.status(db,profile,category)
                    if quota['blocked']:return self.send_json({'error':'Viewing time is finished for today','limit':quota},429)
                    ident=secrets.token_urlsafe(24); now=time.time()
                    channel_session=data.get('channelSession');channel='';queue_entry=None
                    if channel_session is not None:
                        if not isinstance(channel_session,str):raise ValueError('Invalid channel session')
                        channel,_,current=your_tv.get_session(db,channel_session,profile,child['id'] if child else 'parent')
                        key=kind+':'+item
                        if current!=key:raise ValueError('Video is not the current channel item')
                        queue_entry=your_tv.queue_id(db,profile,channel,key)
                        if not queue_entry:return self.send_json({'error':'Channel playlist changed'},409)
                        definition=next((d for d in your_tv.defaults(db,profile) if d['id']==channel and d['enabled']),None)
                        if not definition or not your_tv.matches(definition,channel_candidate):raise ValueError('Channel video no longer eligible')
                        old_progress=channel_candidate.get('progress') or {}
                        if old_progress.get('position',0)>0 and not old_progress.get('completed') and not db.execute(
                            "SELECT 1 FROM activity_sessions WHERE profile_id=? AND kind=? AND item_id=?",(profile,kind,item)).fetchone():
                            db.execute('INSERT OR IGNORE INTO your_tv_preserved VALUES (?,?,?)',(profile,kind,item))
                    db.execute('INSERT INTO activity_sessions(id,profile_id,kind,item_id,started_at,updated_at,recommendation_session,content_category,timer_device,timer_seen,your_tv_channel) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                               (ident,profile,kind,item,now,now,rec,category,child['id'] if child else 'parent',now,channel))
                    if queue_entry:db.execute('INSERT INTO your_tv_activity_queue VALUES (?,?)',(ident,queue_entry))

                return self.send_json({'sessionId':ident,'limit':quota,'progress':channel_candidate.get('progress') if channel_candidate else None})
            if path == '/api/activity/event':
                profile,ident,seq=data.get('profileId'),data.get('sessionId'),data.get('sequence')
                seconds,duration,position=data.get('watchedSeconds'),data.get('duration'),data.get('position')
                finished=data.get('finished',False)
                if not isinstance(profile,str) or not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}',ident) or not isinstance(seq,int) or isinstance(seq,bool) or seq<1 or not isinstance(finished,bool): raise ValueError('Invalid activity event')
                if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in (seconds,duration,position)) or seconds>86400 or duration>=1e9 or position>=1e9: raise ValueError('Invalid playback values')
                playlist_changed=False
                with sqlite3.connect(self.server.db_path) as db:
                    db.execute('BEGIN IMMEDIATE')
                    row=db.execute('SELECT sequence,watched_seconds,started_at,content_category,timer_device,your_tv_channel,kind,item_id,finished FROM activity_sessions WHERE id=? AND profile_id=?',(ident,profile)).fetchone()
                    if not row: return self.send_json({'error':'Unknown activity session'},404)
                    if row[6] not in ('local','jellyfin'): return self.send_json({'error':'Unknown activity session'},404)
                    if child and not self.parent_allowed() and row[4] and row[4]!=child['id']:return self.send_json({'error':'Access denied'},403)
                    db.execute("UPDATE activity_sessions SET timer_seen=?, timer_device=CASE WHEN timer_device='' THEN ? ELSE timer_device END WHERE id=?",(time.time(),child['id'] if child else 'parent',ident))
                    if seq>row[0]:
                        now=time.time(); observed=data.get('observedAt',now)
                        if isinstance(observed,bool) or not isinstance(observed,(int,float)) or not math.isfinite(observed) or observed<row[2]-300 or observed>now+300: observed=now
                        db.execute('UPDATE activity_sessions SET sequence=?,updated_at=MAX(updated_at,?),watched_seconds=MAX(watched_seconds,?),duration=MAX(duration,?),position=?,finished=MAX(finished,?) WHERE id=? AND profile_id=? AND sequence<?',(seq,observed,seconds,duration,position,int(finished),ident,profile,seq))
                        if row[5] and seconds>0:
                            db.execute("""INSERT INTO your_tv_latest VALUES (?,?,?,?,?,?) ON CONFLICT(profile_id,channel_id)
                                DO UPDATE SET activity_id=excluded.activity_id,kind=excluded.kind,item_id=excluded.item_id,started=excluded.started
                                WHERE excluded.started>your_tv_latest.started""",(profile,row[5],ident,row[6],row[7],row[2]))
                        delta=max(0,seconds-row[1])
                        if delta: db.execute('''INSERT INTO activity_minutes VALUES (?,?,?) ON CONFLICT(session_id,minute)
                                                DO UPDATE SET seconds=seconds+excluded.seconds''',(ident,int(observed//60),delta))
                        if finished and not row[8]:
                            if row[5]:
                                entry=db.execute('SELECT queue_id FROM your_tv_activity_queue WHERE activity_id=?',(ident,)).fetchone()
                                if entry:
                                    playlist_changed=bool(db.execute('DELETE FROM your_tv_queue WHERE id=? AND profile_id=? AND channel_id=?',(entry[0],profile,row[5])).rowcount)
                    quota=time_limits.status(db,profile,row[3])
                return self.send_json({'ok':True,'limit':quota})
            if path == '/api/recommendations/reset':
                profile=data.get('profileId')
                if not isinstance(profile,str): raise ValueError('Invalid profile')
                with sqlite3.connect(self.server.db_path) as db:
                    if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone(): return self.send_json({'error':'Unknown profile'},404)
                    db.execute('''INSERT INTO recommendation_epochs VALUES (?,1) ON CONFLICT(profile_id)
                                  DO UPDATE SET epoch=epoch+1''',(profile,))
                    db.execute('INSERT OR REPLACE INTO recommendation_reset_times VALUES (?,?)',(profile,time.time()))
                    db.execute('DELETE FROM recommendation_impressions WHERE profile_id=?',(profile,))
                return self.send_json({'ok':True})
            if path in ('/api/jellyfin/approval', '/api/jellyfin/start', '/api/jellyfin/event'):
                profile = data.get('profileId')
                if not isinstance(profile, str): raise ValueError('Invalid profile')
                with sqlite3.connect(self.server.db_path) as db:
                    if not db.execute('SELECT 1 FROM profiles WHERE id=?', (profile,)).fetchone():
                        return self.send_json({'error': 'Unknown profile'}, 404)
                try:
                    mapping = jellyfin.load_config(Path(self.server.db_path).parent).get(profile)
                    if not mapping: return self.send_json({'error': 'Profile is not connected to Jellyfin'}, 404)
                    if path == '/api/jellyfin/approval':
                        if self.server.protected_mode: return self.send_json({'error':'Use profile approvals'},403)
                        item, approved = data.get('itemId'), data.get('approved')
                        if not isinstance(item, str) or not item.isalnum() or not isinstance(approved, bool): raise ValueError('Invalid approval')
                        if approved:
                            video = jellyfin.get_item(mapping, item)
                            if not isinstance(video, dict) or video.get('Id') != item:
                                return self.send_json({'error': 'Unknown Jellyfin video'}, 404)
                        with sqlite3.connect(self.server.db_path) as db:
                            if approved: db.execute('INSERT OR IGNORE INTO jellyfin_approvals VALUES (?, ?)', (mapping['server'], item))
                            else: db.execute('DELETE FROM jellyfin_approvals WHERE server=? AND item_id=?', (mapping['server'], item))
                        return self.send_json({'ok': True})
                    if path == '/api/jellyfin/start':
                        item = data.get('itemId')
                        transcode=data.get('transcode',False)
                        if not isinstance(transcode,bool): raise ValueError('Invalid playback mode')
                        if not isinstance(item, str) or not item.isalnum(): raise ValueError('Invalid item')
                        with sqlite3.connect(self.server.db_path) as db:
                            approved=db.execute('SELECT 1 FROM jellyfin_approvals WHERE server=? AND item_id=?', (mapping['server'], item)).fetchone()
                        if self.server.protected_mode or (child and not self.parent_allowed()): approved=self.jellyfin_allowed(profile,item,mapping)
                        if not approved: return self.send_json({'error': 'Video is not approved'}, 403)
                        video = jellyfin.get_item(mapping, item)
                        if not isinstance(video, dict) or video.get('Id') != item:
                            return self.send_json({'error': 'Unknown Jellyfin video'}, 404)
                        category=time_limits.category('jellyfin',video.get('Type'))
                        if category is None:return self.send_json({'error':'Unable to classify this video'},503)
                        with sqlite3.connect(self.server.db_path) as db:
                            if time_limits.status(db,profile,category)['blocked']:return self.send_json({'error':'Viewing time is finished for today'},429)
                        info = jellyfin.transcode_playback_info(mapping,item) if transcode else jellyfin.playback_info(mapping, item)
                        position = max(0, ((video.get('UserData') or {}).get('PlaybackPositionTicks') or 0) / jellyfin.TICKS_PER_SECOND)
                        with self.server.jellyfin_lock:
                            if transcode: jellyfin.report_playback(mapping, 'start', item, position, info['PlaySessionId'],play_method='Transcode')
                            else: jellyfin.report_playback(mapping, 'start', item, position, info['PlaySessionId'])
                            current = jellyfin.get_item(mapping, item).get('UserData') or {}
                            observed = (current.get('PlaybackPositionTicks') or 0) / jellyfin.TICKS_PER_SECOND
                            ident = secrets.token_urlsafe(24)
                            with sqlite3.connect(self.server.db_path) as db:
                                old=[r[0] for r in db.execute('SELECT id FROM jellyfin_sessions WHERE profile=? AND item=? AND closed=0',(profile,item))]
                                db.execute('UPDATE jellyfin_sessions SET closed=1 WHERE profile=? AND item=?', (profile,item))
                                db.execute('INSERT INTO jellyfin_sessions(id,profile,item,server,user_id,play_id,last_position,last_played,transcode,content_category) VALUES(?,?,?,?,?,?,?,?,?,?)', (ident,profile,item,mapping['server'],mapping['userId'],info['PlaySessionId'],observed,int(bool(current.get('Played'))),int(transcode),category))
                            for old_id in old: self.server.jellyfin_hls.pop(old_id,None)
                            if transcode: self.server.jellyfin_hls[ident]={'master':info['path'],'resources':{}}
                        return self.send_json({'sessionId': ident, 'streamUrl': '/api/jellyfin/stream/' + ident, 'position': position,
                                               'completed': bool((video.get('UserData') or {}).get('Played'))})
                    ident, seq, kind = data.get('sessionId'), data.get('sequence'), data.get('event')
                    position, paused = data.get('position'), data.get('paused', False)
                    if not isinstance(ident, str) or not isinstance(seq, int) or isinstance(seq, bool) or seq < 1 or kind not in ('progress', 'stop') or isinstance(position, bool) or not isinstance(position, (int, float)) or not math.isfinite(position) or not 0 <= position < 1e9 or not isinstance(paused, bool): raise ValueError('Invalid playback event')
                    with self.server.jellyfin_lock:
                        session = self.jellyfin_session(ident)
                        if not session or session['profile'] != profile or session['server'] != mapping['server'] or session['user_id'] != mapping['userId']:
                            return self.send_json({'error': 'Playback session expired'}, 404)
                        if seq == session['seq']: return self.send_json({'ok': True})
                        if session['closed'] or seq < session['seq']: return self.send_json({'error': 'Stale playback event'}, 409)
                        current = jellyfin.get_item(mapping, session['item']).get('UserData') or {}
                        observed = (current.get('PlaybackPositionTicks') or 0) / jellyfin.TICKS_PER_SECOND
                        if session['last_position'] is not None and (abs(observed-session['last_position']) > 3 or bool(current.get('Played')) != bool(session['last_played'])) and abs(observed-position) > 3:
                            return self.send_json({'error': 'Progress changed on another device; review the pending update'}, 409)
                        if session['transcode']: jellyfin.report_playback(mapping, kind, session['item'], position, session['play_id'], paused, play_method='Transcode')
                        else: jellyfin.report_playback(mapping, kind, session['item'], position, session['play_id'], paused)
                        current = jellyfin.get_item(mapping, session['item']).get('UserData') or {}
                        observed = (current.get('PlaybackPositionTicks') or 0) / jellyfin.TICKS_PER_SECOND
                        with sqlite3.connect(self.server.db_path) as db:
                            db.execute('UPDATE jellyfin_sessions SET seq=?, closed=?, last_position=?, last_played=? WHERE id=?', (seq, int(kind=='stop'), observed, int(bool(current.get('Played'))), ident))
                        if kind=='stop': self.server.jellyfin_hls.pop(ident,None)
                    return self.send_json({'ok': True})
                except jellyfin.JellyfinError:
                    return self.send_json({'error': 'Jellyfin is unavailable or its connection needs attention'}, 502)
            if path == '/api/tv/favourite':
                profile,kind,item,enabled=data.get('profileId'),data.get('kind'),data.get('itemId'),data.get('favourite')
                if not isinstance(profile,str) or kind not in ('local','jellyfin') or not isinstance(item,str) or not item or not isinstance(enabled,bool):raise ValueError('Invalid favourite')
                with sqlite3.connect(self.server.db_path) as db:
                    if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone():return self.send_json({'error':'Unknown profile'},404)
                mapping=None
                if kind=='local':
                    if item not in media_index(self.server.media_dir) or ((self.server.protected_mode or child) and not self.child_local_allowed(profile,item)):return self.send_json({'error':'Video not approved'},403)
                else:
                    if not item.isascii() or not item.isalnum():raise ValueError('Invalid video')
                    mapping=jellyfin.load_config(Path(self.server.db_path).parent).get(profile)
                    if not mapping:return self.send_json({'error':'Jellyfin not connected'},404)
                    if self.server.protected_mode or child:
                        approved=self.jellyfin_allowed(profile,item,mapping)
                    else:
                        with sqlite3.connect(self.server.db_path) as db: approved=bool(db.execute('SELECT 1 FROM jellyfin_approvals WHERE server=? AND item_id=?',(mapping['server'],item)).fetchone())
                    if not approved:return self.send_json({'error':'Video not approved'},403)
                with self.server.cache_policy_lock, sqlite3.connect(self.server.db_path) as db:
                    db.execute('BEGIN IMMEDIATE')
                    if kind=='jellyfin':
                        if enabled:db.execute('INSERT OR IGNORE INTO jellyfin_favourites VALUES (?,?,?)',(profile,mapping['server'],item))
                        else:db.execute('DELETE FROM jellyfin_favourites WHERE profile_id=? AND server=? AND item_id=?',(profile,mapping['server'],item))
                    else:
                        table='local_preferences' if kind=='local' else 'discovery_preferences'
                        row=db.execute('SELECT settings FROM '+table+' WHERE profile_id=?',(profile,)).fetchone()
                        settings=json.loads(row[0]) if row else ({'favouriteIds':[],'dismissedIds':[]} if kind=='local' else dict(DEFAULT_DISCOVERY))
                        ident=item if kind=='local' else int(item)
                        favourites=[v for v in settings.get('favouriteIds',[]) if v!=ident]
                        if enabled:
                            if len(favourites)>=100:raise ValueError('Too many favourites')
                            favourites.append(ident)
                        settings['favouriteIds']=favourites
                        db.execute('INSERT OR REPLACE INTO '+table+' VALUES (?,?)',(profile,json.dumps(settings,ensure_ascii=False)))
                return self.send_json({'ok':True,'favourite':enabled})
            if path == '/api/tv/profiles/delete':
                profile=data.get('profileId')
                if not isinstance(profile,str) or profile=='family': raise ValueError('The Family profile cannot be removed')
                with sqlite3.connect(self.server.db_path) as db:
                    if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone(): return self.send_json({'error':'Unknown profile'},404)
                    db.execute('DELETE FROM activity_minutes WHERE session_id IN (SELECT id FROM activity_sessions WHERE profile_id=?)',(profile,))
                    db.execute('DELETE FROM your_tv_activity_queue WHERE activity_id IN (SELECT id FROM activity_sessions WHERE profile_id=?)',(profile,))
                    for table in ('progress','discovery_preferences','local_preferences','jellyfin_favourites','recommendation_sessions','recommendation_epochs','recommendation_reset_times','recommendation_impressions','promotion_preferences','viewing_limits','your_tv_definitions','your_tv_initialized','your_tv_sessions','your_tv_latest','your_tv_preserved','your_tv_offers','your_tv_queue','activity_sessions','content_reviews','jellyfin_age_policies','jellyfin_series_overrides'):db.execute('DELETE FROM '+table+' WHERE profile_id=?',(profile,))
                    db.execute('DELETE FROM profiles WHERE id=?',(profile,))
                return self.send_json({'ok':True})
            if path == '/api/tv/local-preferences':
                profile=data.get('profileId'); settings=data.get('settings'); ids=set(media_index(self.server.media_dir))
                if not isinstance(settings,dict): raise ValueError('Invalid preferences')
                for field in ('favouriteIds','dismissedIds'):
                    values=settings.get(field)
                    if not isinstance(values,list) or len(values)>1000 or any(not isinstance(v,str) or v not in ids for v in values): raise ValueError('Invalid local preferences')
                with sqlite3.connect(self.server.db_path) as db:
                    if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone(): return self.send_json({'error':'Unknown profile'},404)
                    db.execute('INSERT OR REPLACE INTO local_preferences VALUES (?,?)',(profile,json.dumps(settings)))
                return self.send_json({'ok':True})
            if path == '/api/profiles':
                name = str(data.get('name','')).strip()
                if not name or len(name) > 40: raise ValueError('Name must be 1–40 characters')
                ident = secrets.token_hex(8)
                with sqlite3.connect(self.server.db_path) as db: db.execute('INSERT INTO profiles(id,name) VALUES (?,?)',(ident,name))
                return self.send_json({'id':ident,'name':name}, 201)
            if path == '/api/progress':
                profile, media_id = data.get('profileId'), data.get('mediaId')
                if media_id not in media_index(self.server.media_dir): return self.send_json({'error':'Unknown video'}, 404)
                elif child and not self.parent_allowed() and not self.child_local_allowed(profile,media_id): return self.send_json({'error':'Video not approved'},403)
                position = float(data.get('position', 0)); duration = float(data.get('duration', 0))
                if not 0 <= position < 1e9 or not 0 <= duration < 1e9: raise ValueError('Invalid position')
                completed = bool(data.get('completed',False))
                with sqlite3.connect(self.server.db_path) as db:
                    if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone(): return self.send_json({'error':'Unknown profile'},404)
                    db.execute('''INSERT INTO progress VALUES(?,?,?,?,?,?) ON CONFLICT(profile_id,media_id) DO UPDATE SET position=excluded.position,duration=excluded.duration,completed=excluded.completed,updated_at=excluded.updated_at''',(profile,media_id,position,duration,int(completed),time.time()))
                return self.send_json({'ok':True})
            if path == '/api/approval':
                if self.server.protected_mode: return self.send_json({'error':'Use profile approvals'},403)
                media_id = data.get('mediaId')
                if media_id not in media_index(self.server.media_dir): return self.send_json({'error':'Unknown video'}, 404)
                if not isinstance(data.get('approved'), bool): raise ValueError('Invalid approval')
                with sqlite3.connect(self.server.db_path) as db: db.execute('INSERT OR REPLACE INTO approvals VALUES (?,?)',(media_id,int(data['approved'])))
                return self.send_json({'ok':True})
            return self.send_json({'error':'Not found'},404)
        except (ValueError, TypeError, json.JSONDecodeError): return self.send_json({'error':'Invalid request'},400)
    def file(self, path, ranged=False, content_type=None, check=None):
        try: size = path.stat().st_size
        except OSError: return self.send_json({'error':'Not found'},404)
        start,end=0,size-1; status=200
        if ranged and self.headers.get('Range'):
            m=re.fullmatch(r'bytes=(\d*)-(\d*)',self.headers['Range'])
            if not m or (not m.group(1) and not m.group(2)): return self.range_error(size)
            if m.group(1):
                start=int(m.group(1)); end=min(int(m.group(2)),size-1) if m.group(2) else size-1
            else:
                suffix=int(m.group(2)); start=max(size-suffix,0)
            if start>=size or end<start: return self.range_error(size)
            status=206
        self.send_response(status); self.send_header('Content-Type',content_type or mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Cache-Control','no-store')
        if ranged: self.send_header('Accept-Ranges','bytes')
        if status==206: self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.send_header('Content-Length',str(max(0,end-start+1))); self.end_headers()
        try:
            with path.open('rb') as f:
                f.seek(start); remaining=end-start+1
                while remaining>0:
                    if check and not check(): break
                    data=f.read(min(65536,remaining))
                    if not data: break
                    self.wfile.write(data); remaining-=len(data)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLEOFError): pass
    def range_error(self,size):
        self.send_response(416); self.send_header('Content-Range', f'bytes */{size}'); self.send_header('Content-Length','0'); self.end_headers()

def main():
    parser=argparse.ArgumentParser(description='Run the Kidoo home media prototype')
    parser.add_argument('--media-dir',required=True,help='Folder containing your video files')
    parser.add_argument('--data-dir',default=str(Path.home()/'.kidoo'),help='Folder for profiles and playback history')
    parser.add_argument('--host',default='127.0.0.1'); parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--protected-mode',action='store_true',help='Require paired devices or administrator PIN sign-in')
    parser.add_argument('--tls-cert',help='PEM TLS certificate chain')
    parser.add_argument('--tls-key',help='PEM TLS private key')
    args=parser.parse_args(); media=Path(args.media_dir).expanduser().resolve(); data=Path(args.data_dir).expanduser().resolve()
    if bool(args.tls_cert) != bool(args.tls_key): parser.error('Specify both --tls-cert and --tls-key')
    if args.protected_mode and not args.tls_cert: parser.error('Protected mode requires a trusted TLS certificate')
    if not media.is_dir(): parser.error('Media directory does not exist')
    password=os.environ.get('KIDOO_PASSWORD','')
    if args.host not in ('127.0.0.1','localhost','::1') and not password and not args.protected_mode: parser.error('Set KIDOO_PASSWORD before listening on your network')
    data.mkdir(parents=True,exist_ok=True)
    server=KidooServer((args.host,args.port),media,data/'kidoo.db',password)
    if args.protected_mode:
        with sqlite3.connect(server.db_path) as db:
            configured=db.execute('SELECT 1 FROM security_pin').fetchone() and db.execute('SELECT 1 FROM security_administrators').fetchone()
        if not configured:
            server.server_close();parser.error('Set a parent PIN and create an administrator before protected mode')
        server.protected_mode=True
    if args.tls_cert:
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version=ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.tls_cert,args.tls_key)
        server.tls_cert_path=args.tls_cert
        server.socket=context.wrap_socket(server.socket,server_side=True)
    scheme='https' if args.tls_cert else 'http'
    print(f'Kidoo is running at {scheme}://{args.host}:{server.server_port}/ (trusted network only)',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
if __name__=='__main__': main()
