"""Per-profile daily viewing limits; timestamps use the server's local timezone."""
import math
import time

CATEGORIES=('movies','series','youtube','other')
FIELDS=('total',)+CATEGORIES
PUBLIC_FIELDS=('total','movies','series','other')

def category(kind, media_type=None):
    if kind in ('local','jellyfin') and media_type=='Movie': return 'movies'
    if kind in ('local','jellyfin') and media_type=='Episode': return 'series'
    if kind=='jellyfin' and not media_type: return None
    return 'other'

def validate(value):
    if not isinstance(value,dict) or set(value)!=set(PUBLIC_FIELDS): raise ValueError('Invalid limits')
    out={}
    for key in PUBLIC_FIELDS:
        item=value[key]
        if item is not None and (isinstance(item,bool) or not isinstance(item,int) or not 0<=item<=1440):
            raise ValueError('Limits must be whole minutes between 0 and 1440')
        out[key]=item
    return out

def bounds(now=None):
    now=time.time() if now is None else now
    local=time.localtime(now)
    start=time.mktime((local.tm_year,local.tm_mon,local.tm_mday,0,0,0,0,0,-1))
    # Normalize the next calendar day so daylight-saving changes are respected.
    end=time.mktime((local.tm_year,local.tm_mon,local.tm_mday+1,0,0,0,0,0,-1))
    return start,end

def status(db,profile,selected=None,now=None):
    row=db.execute('SELECT total,movies,series,youtube,other FROM viewing_limits WHERE profile_id=?',(profile,)).fetchone()
    limits=dict(zip(FIELDS,row)) if row else dict.fromkeys(FIELDS)
    start,end=bounds(now)
    totals={key:0.0 for key in CATEGORIES}; unknown=0.0
    for kind,amount in db.execute('''SELECT s.content_category,COALESCE(SUM(m.seconds),0)
        FROM activity_sessions s JOIN activity_minutes m ON m.session_id=s.id
        WHERE s.profile_id=? AND m.minute>=? AND m.minute<? GROUP BY s.content_category''',
        (profile,math.ceil(start/60),math.ceil(end/60))):
        if kind in totals: totals[kind]+=amount
        else: unknown+=amount
    total=sum(totals.values())+unknown
    usage={'total':total,**totals,'unclassified':unknown}
    # Older Jellyfin activity may lack a category. Conservatively count it against
    # each local category until the day resets, while counting it only once in total.
    remaining={key:None if limits[key] is None else max(0.0,limits[key]*60-usage[key]-(unknown if key in ('movies','series','other') else 0)) for key in FIELDS}
    relevant=[remaining['total']]
    if selected in CATEGORIES: relevant.append(remaining[selected])
    active=[v for v in relevant if v is not None]
    # Retain historical online time in the daily total, but expose and enforce
    # only Community categories. The legacy column remains untouched on disk.
    return {'limits':{key:limits[key] for key in PUBLIC_FIELDS},
            'usedSeconds':{key:usage[key] for key in (*PUBLIC_FIELDS,'unclassified')},
            'remainingSeconds':{key:remaining[key] for key in PUBLIC_FIELDS},
            'remaining':min(active) if active else None,'blocked':bool(active and min(active)<=0),
            'enabled':any(limits[key] is not None for key in PUBLIC_FIELDS),'dayEndsAt':end,
            'timezone':time.tzname[time.localtime(time.time() if now is None else now).tm_isdst>0]}

def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS viewing_limits (
        profile_id TEXT PRIMARY KEY, total INTEGER, movies INTEGER, series INTEGER, youtube INTEGER, other INTEGER)''')
    columns={row[1] for row in db.execute('PRAGMA table_info(activity_sessions)')}
    if 'content_category' not in columns:
        db.execute("ALTER TABLE activity_sessions ADD COLUMN content_category TEXT NOT NULL DEFAULT ''")
    if 'timer_device' not in columns:
        db.execute("ALTER TABLE activity_sessions ADD COLUMN timer_device TEXT NOT NULL DEFAULT ''")
    if 'timer_seen' not in columns:
        db.execute("ALTER TABLE activity_sessions ADD COLUMN timer_seen REAL NOT NULL DEFAULT 0")
    db.execute("UPDATE activity_sessions SET content_category=kind WHERE content_category='' AND kind='youtube'")
    db.execute("UPDATE activity_sessions SET content_category='other' WHERE content_category='' AND kind='local'")
    columns={row[1] for row in db.execute('PRAGMA table_info(jellyfin_sessions)')}
    if 'content_category' not in columns:
        db.execute("ALTER TABLE jellyfin_sessions ADD COLUMN content_category TEXT NOT NULL DEFAULT ''")
