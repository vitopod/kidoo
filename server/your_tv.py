"""Per-profile continuous channels. Selection never grants content approval."""
import json
import re
import secrets
import sqlite3
import time
from tv_catalogue import genre_interests

DEFAULTS=[
    {'id':'surprise','name':'Surprise TV','topics':['all'], 'languages':[], 'sources':['jellyfin','local']},
    {'id':'animals','name':'Animals','topics':['animals','nature'], 'languages':[], 'sources':['jellyfin','local']},
    {'id':'space','name':'Space','topics':['space','science'], 'languages':[], 'sources':['jellyfin','local']},
    {'id':'science','name':'Science','topics':['science','questions'], 'languages':[], 'sources':['jellyfin','local']},
    {'id':'cartoons','name':'Cartoons','topics':['art','animation','stories'], 'languages':[], 'sources':['jellyfin','local']},
    {'id':'making','name':'Make & Create','topics':['making','art'], 'languages':[], 'sources':['jellyfin','local']},
    {'id':'music','name':'Music','topics':['music'], 'languages':[], 'sources':['jellyfin','local']},
]

def community_defaults():
    """Defaults for locally hosted and Jellyfin content."""
    return [dict(d, topics=list(d['topics']), languages=list(d['languages']), sources=['jellyfin','local']) for d in DEFAULTS]

def community_definition(definition):
    """Prepare a saved definition for Community without modifying its input.

    Channels with no supported source are disabled, rather than silently
    changing which content they select. Their other settings remain intact.
    This helper does not migrate a database or change a running installation.
    """
    result=validate(definition, allow_legacy=True)
    supported=[source for source in result['sources'] if source in ('local','jellyfin')]
    if supported:
        result['sources']=supported
    else:
        result['sources']=['jellyfin','local']
        result['enabled']=False
    return result

def init(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS your_tv_definitions (
      profile_id TEXT NOT NULL, id TEXT NOT NULL, definition TEXT NOT NULL, PRIMARY KEY(profile_id,id));
      CREATE TABLE IF NOT EXISTS your_tv_initialized (profile_id TEXT PRIMARY KEY);
      CREATE TABLE IF NOT EXISTS your_tv_sessions (
      id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, device_id TEXT NOT NULL, channel_id TEXT NOT NULL,
      history TEXT NOT NULL DEFAULT '[]', current_key TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS your_tv_latest (
      profile_id TEXT NOT NULL, channel_id TEXT NOT NULL, activity_id TEXT NOT NULL, kind TEXT NOT NULL,
      item_id TEXT NOT NULL, started REAL NOT NULL, PRIMARY KEY(profile_id,channel_id));
      CREATE TABLE IF NOT EXISTS your_tv_preserved (
      profile_id TEXT NOT NULL, kind TEXT NOT NULL, item_id TEXT NOT NULL,
      PRIMARY KEY(profile_id,kind,item_id));
      CREATE TABLE IF NOT EXISTS your_tv_offers (
      id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, device_id TEXT NOT NULL,
      channel_id TEXT NOT NULL, item_key TEXT NOT NULL, created REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS your_tv_queue (
      id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, channel_id TEXT NOT NULL,
      item_key TEXT NOT NULL, youtube_id TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL,
      UNIQUE(profile_id,channel_id,item_key));
      CREATE INDEX IF NOT EXISTS your_tv_queue_order ON your_tv_queue(profile_id,channel_id,position);
      CREATE TABLE IF NOT EXISTS your_tv_activity_queue (
      activity_id TEXT PRIMARY KEY, queue_id TEXT NOT NULL);''')
    cols={r[1] for r in db.execute('PRAGMA table_info(activity_sessions)')}
    if 'your_tv_channel' not in cols: db.execute("ALTER TABLE activity_sessions ADD COLUMN your_tv_channel TEXT NOT NULL DEFAULT ''")
    session_cols={r[1] for r in db.execute('PRAGMA table_info(your_tv_sessions)')}
    if 'last_request' not in session_cols: db.execute("ALTER TABLE your_tv_sessions ADD COLUMN last_request TEXT NOT NULL DEFAULT ''")
    if 'preview_key' not in session_cols: db.execute("ALTER TABLE your_tv_sessions ADD COLUMN preview_key TEXT NOT NULL DEFAULT ''")

def defaults(db,profile):
    if not db.execute('SELECT 1 FROM your_tv_initialized WHERE profile_id=?',(profile,)).fetchone():
        db.execute('INSERT OR IGNORE INTO your_tv_initialized VALUES (?)',(profile,))
        for d in community_defaults():
            db.execute('INSERT OR IGNORE INTO your_tv_definitions VALUES (?,?,?)',(profile,d['id'],json.dumps(dict(d,enabled=True))))
    # Read legacy definitions without exposing unsupported sources or modifying
    # the original record. An online-only channel stays disabled.
    return [community_definition(json.loads(r[0])) for r in db.execute('SELECT definition FROM your_tv_definitions WHERE profile_id=? ORDER BY id',(profile,))]

def validate(value, allow_legacy=False):
    if not isinstance(value,dict): raise ValueError('Invalid channel')
    ident=value.get('id',''); name=value.get('name',''); enabled=value.get('enabled',True)
    if not isinstance(ident,str) or not re.fullmatch(r'[a-z0-9-]{1,40}',ident): raise ValueError('Invalid channel ID')
    if not isinstance(name,str) or not name.strip() or len(name.strip())>40 or not isinstance(enabled,bool): raise ValueError('Invalid channel name')
    result={'id':ident,'name':name.strip(),'enabled':enabled}
    sources={'jellyfin','local'} | ({'youtube'} if allow_legacy else set())
    for field,allowed in [('topics',None),('languages',{'English','French','Italian'}),('sources',sources)]:
        values=value.get(field,[])
        if not isinstance(values,list) or len(values)>20 or any(not isinstance(v,str) or not v.strip() or len(v)>40 or (allowed and v not in allowed) for v in values): raise ValueError('Invalid '+field)
        result[field]=list(dict.fromkeys(v.strip() for v in values))
    if not result['topics'] or not result['sources']: raise ValueError('Choose at least one topic and source')
    return result

def matches(definition,item):
    source=item.get('kind')
    if source not in ('jellyfin','local'): return False
    if source not in definition['sources']: return False
    langs=definition.get('languages',[])
    # Unknown local/Jellyfin language is not inferred.
    if langs: return False
    labels={str(x).casefold() for x in item.get('interests',[])+item.get('tags',[])+[item.get('topic','')]}
    labels.update(genre_interests(item.get('genres',[])))
    if item.get('mediaType')=='Episode': labels.add('animation') if 'animation' in labels else None
    wanted={v.casefold() for v in definition['topics']}
    return 'all' in wanted or bool(wanted & labels)

def labels(item):
    result={str(x).casefold() for x in item.get('interests',[])+item.get('tags',[])+[item.get('topic','')] if x}
    return result | set(genre_interests(item.get('genres',[])))

def duration_seconds(item):
    for value in (item.get('durationSeconds'),item.get('duration'),(item.get('progress') or {}).get('duration')):
        if isinstance(value,(int,float)) and not isinstance(value,bool) and 0<value<1e9:return float(value)
    return None

def artwork_key(definition):
    """Choose topic artwork without implying any particular video will play."""
    topics={str(t).casefold() for t in definition.get('topics',[])}
    for key,values in (('music',{'music'}),('animals',{'animals','nature','planet'}),('space',{'space'}),
                       ('science',{'science','questions','maths'}),('cartoons',{'animation','stories'}),
                       ('making',{'making','art','creativity'})):
        if topics & values:return key
    return 'surprise'

def viewing_signals(db,profile,items):
    """A small, local signal from actual viewing, never a content approval."""
    by_key={v['key']:v for v in items}; interests={};recent=[]
    rows=db.execute('''SELECT kind,item_id,SUM(watched_seconds),MAX(started_at)
        FROM activity_sessions WHERE profile_id=? AND watched_seconds>0 AND started_at>=?
        GROUP BY kind,item_id ORDER BY MAX(started_at) DESC LIMIT 100''',(profile,time.time()-90*86400)).fetchall()
    for kind,ident,seconds,_ in rows:
        if kind not in ('local','jellyfin'): continue
        key=kind+':'+ident
        recent.append(key)
        item=by_key.get(key)
        if item and seconds>=20:
            for topic in labels(item):interests[topic]=min(5,interests.get(topic,0)+1)
    return interests,recent

def select(definition,items,history,interests=None,recent=None):
    candidates=[item for item in items if matches(definition,item)]
    if not candidates:return None
    # Prioritize unseen in this session and recently played titles last. If the
    # library is small, repeats remain possible rather than ending the channel.
    unseen=[v for v in candidates if v['key'] not in history]
    pool=unseen or candidates
    recent=(recent or [])[:12]
    fresh=[v for v in pool if v['key'] not in recent]
    if fresh:pool=fresh
    if not history:
        # A known short first programme makes it more likely the next one can
        # be prepared while watching. Unknown duration is never called short.
        short=[v for v in pool if duration_seconds(v) is not None and duration_seconds(v)<=12*60]
        if short:pool=short
        else:
            unknown=[v for v in pool if duration_seconds(v) is None]
            if unknown:pool=unknown
    if history:
        previous=next((v for v in candidates if v['key']==history[-1]),None)
        different=[v for v in pool if previous and v['key']!=previous['key'] and v.get('creator')!=previous.get('creator')]
        if different:pool=different
        else:
            different=[v for v in pool if v['key']!=history[-1]]
            if different:pool=different
    # One in three choices deliberately explores; otherwise interests offer only
    # a modest weight and every candidate retains a chance of being selected.
    weights=[]
    for item in pool:
        affinity=sum((interests or {}).get(t,0) for t in labels(item))
        weights.extend([item]*(1 if len(history)%3==2 else 1+min(2,affinity)))
    return secrets.choice(weights)

def start(db,profile,device,channel):
    session=secrets.token_urlsafe(24)
    db.execute('INSERT INTO your_tv_sessions(id,profile_id,device_id,channel_id,created) VALUES (?,?,?,?,?)',(session,profile,device,channel,time.time()))
    return session

def offer(db,profile,device,channel,key,now=None):
    now=time.time() if now is None else now
    db.execute('DELETE FROM your_tv_offers WHERE created<?',(now-6*3600,))
    db.execute('''DELETE FROM your_tv_offers WHERE id IN
        (SELECT id FROM your_tv_offers WHERE profile_id=? AND device_id=? ORDER BY created DESC LIMIT -1 OFFSET 180)''',(profile,device))
    ident=secrets.token_urlsafe(24)
    db.execute('INSERT INTO your_tv_offers VALUES (?,?,?,?,?,?)',(ident,profile,device,channel,key,now))
    return ident

def offered_item(db,ident,profile,device,channel,now=None):
    now=time.time() if now is None else now
    row=db.execute('''SELECT item_key FROM your_tv_offers WHERE id=? AND profile_id=? AND device_id=?
        AND channel_id=? AND created>=?''',(ident,profile,device,channel,now-6*3600)).fetchone()
    return row[0] if row else None

def reconcile_queue(db,profile,definition,candidates,interests=None,recent=None):
    """Keep the existing order, remove ineligible entries, and fill to three."""
    channel=definition['id']
    by_key={v['key']:v for v in candidates if matches(definition,v)}
    rows=db.execute('SELECT id,item_key FROM your_tv_queue WHERE profile_id=? AND channel_id=? ORDER BY position',(profile,channel)).fetchall()
    for ident,key in rows:
        if key not in by_key:db.execute('DELETE FROM your_tv_queue WHERE id=?',(ident,))
    retained=[(ident,key) for ident,key in rows if key in by_key][:3]
    for ident,key in rows:
        if key in by_key and (ident,key) not in retained:db.execute('DELETE FROM your_tv_queue WHERE id=?',(ident,))
    keys=[key for _,key in retained]
    while len(keys)<3:
        available=[v for key,v in by_key.items() if key not in keys]
        if not available:break
        item=select(definition,available,keys,interests,recent)
        if not item:break
        key=item['key'];source_id=''
        ident=secrets.token_urlsafe(24)
        db.execute('INSERT INTO your_tv_queue VALUES (?,?,?,?,?,?)',(ident,profile,channel,key,source_id,len(keys)))
        retained.append((ident,key));keys.append(key)
    for position,(ident,_) in enumerate(retained):db.execute('UPDATE your_tv_queue SET position=? WHERE id=?',(position,ident))
    return [by_key[key] for _,key in retained]

def queue_id(db,profile,channel,key):
    row=db.execute('SELECT id FROM your_tv_queue WHERE profile_id=? AND channel_id=? AND item_key=?',(profile,channel,key)).fetchone()
    return row[0] if row else None

def get_session(db,ident,profile,device):
    row=db.execute('SELECT channel_id,history,current_key FROM your_tv_sessions WHERE id=? AND profile_id=? AND device_id=?',(ident,profile,device)).fetchone()
    if not row:raise ValueError('Unknown channel session')
    return row[0],json.loads(row[1]),row[2]
