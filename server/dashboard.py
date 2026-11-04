"""Profile-scoped parent dashboard. All activity stays on the Kidoo server."""
from datetime import datetime
import re
import sqlite3
import time

import jellyfin


def timestamp(value):
    if not value: return None
    try: return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    except (ValueError,TypeError,AttributeError): return None


def dashboard(db_path, profile, days, media, catalogue, channels, mapping):
    now=time.time(); cutoff=now-days*86400 if days else None
    with sqlite3.connect(db_path) as db:
        db.row_factory=sqlite3.Row
        sessions=[dict(r) for r in db.execute("SELECT * FROM activity_sessions WHERE profile_id=? AND kind IN ('local','jellyfin')",(profile,))]
        buckets=[dict(r) for r in db.execute('''SELECT m.session_id,m.minute,m.seconds FROM activity_minutes m
            JOIN activity_sessions s ON s.id=m.session_id WHERE s.profile_id=?''',(profile,))]
        progress=[dict(r) for r in db.execute("SELECT * FROM progress WHERE profile_id=? AND media_id NOT LIKE 'youtube:%'",(profile,))]
        reviews={(r['kind'],r['item_id']):dict(r) for r in db.execute('SELECT * FROM content_reviews WHERE profile_id=?',(profile,))}
        local_approved={r[0] for r in db.execute('SELECT media_id FROM approvals WHERE approved=1')}
        approved_jellyfin={r[0] for r in db.execute('SELECT item_id FROM jellyfin_approvals WHERE server=?',((mapping or {}).get('server',''),))}
    items={}; sources={'jellyfin':'Jellyfin','local':'Family library'}

    def item(kind, ident):
        key=(kind,str(ident))
        if key in items: return items[key]
        record={'kind':kind,'id':str(ident),'source':sources[kind],'title':'Unavailable video',
                'summary':'','rating':None,'topics':[],'approved':None,'thumbnail':'',
                'lastAt':None,'position':None,'duration':None,'completed':False,
                'measuredSeconds':None,'sessions':0,'fullViews':0,'playCount':None,
                'review':{'status':'unreviewed','note':''}}
        if kind=='local' and str(ident) in media:
            path=media[str(ident)]['path']
            has_cover=any(path.with_suffix(ext).is_file() and not path.with_suffix(ext).is_symlink() for ext in ('.jpg','.png','.jpeg'))
            record.update(title=media[str(ident)]['title'],approved=str(ident) in local_approved,
                          thumbnail=('/api/tv/artwork/'+str(ident)) if has_cover else '')
        if key in reviews:
            review=reviews[key];record['review']={'status':review['status'],'note':review['note']}
        items[key]=record
        return record

    def update_last(record, at):
        if at and (not record['lastAt'] or at>record['lastAt']): record['lastAt']=at

    linked={r['recommendation_session']:r for r in sessions if r['recommendation_session']}
    events=[];measured_total=0;recorded_sessions=set();by_session={}
    for bucket in buckets: by_session.setdefault(bucket['session_id'],[]).append(bucket)
    def add_time(at,seconds):
        nonlocal measured_total
        measured_total+=seconds
        if at>=now-8*86400: events.append({'at':at,'seconds':seconds})
    for s in sessions:
        if s['watched_seconds']<=0: continue
        minutes=by_session.get(s['id'],[])
        selected=[b for b in minutes if not cutoff or b['minute']*60+60>cutoff]
        if minutes and not selected: continue
        if not minutes and cutoff and s['updated_at']<cutoff: continue
        seconds=sum(b['seconds'] for b in selected) if minutes else s['watched_seconds']
        if seconds<=0:continue
        v=item(s['kind'],s['item_id']); update_last(v,s['updated_at'])
        v['measuredSeconds']=(v['measuredSeconds'] or 0)+seconds;v['sessions']+=1;recorded_sessions.add(('activity',s['id']))
        if s['duration']>0 and seconds>=s['duration']*.8: v['fullViews']+=1
        if s['duration']>0:v['duration']=s['duration']
        if s['finished']:v['completed']=True
        if minutes:
            for bucket in selected:add_time(bucket['minute']*60,bucket['seconds'])
        else:add_time(s['updated_at'],seconds)
    for p in progress:
        mid=p['media_id'];kind='local';ident=mid
        if not p['completed'] and p['position']<=0:continue
        if cutoff and p['updated_at']<cutoff:continue
        v=item(kind,ident);update_last(v,p['updated_at']);v['position']=p['position']
        if p['duration']:v['duration']=p['duration']
        v['completed']=bool(p['completed']) or v['completed']

    jellyfin_available=bool(mapping); jellyfin_error=False
    if mapping:
        try:
            start=0
            while True:
                response=jellyfin.list_items(mapping,start,1000); batch=response['items']
                for j in batch:
                    state=j['progress']; at=timestamp(state.get('updatedAt'))
                    if not (state.get('completed') or state.get('position',0)>0 or state.get('playCount',0)>0):continue
                    if cutoff and (not at or at<cutoff):continue
                    ident=j['id'].split(':',1)[1];v=item('jellyfin',ident);update_last(v,at)
                    v.update(title=j.get('title') or 'Untitled',summary=j.get('overview') or '',
                             rating=j.get('rating') or None,topics=j.get('genres') or [],approved=ident in approved_jellyfin,
                             thumbnail=('/api/jellyfin/artwork/'+profile+'/'+ident) if j.get('hasArtwork') else '',
                             duration=j.get('duration') or v['duration'],position=state.get('position'),
                             completed=bool(state.get('completed')) or v['completed'],playCount=state.get('playCount') or None,
                             series=j.get('series'))
                start+=len(batch)
                if not batch or start>=response['total']:break
        except (jellyfin.JellyfinError,ValueError,TypeError): jellyfin_error=True
    # An activity session may exist before Jellyfin reports its item as watched.
    if mapping:
        for (kind,ident),v in items.items():
            if kind!='jellyfin' or v['title']!='Unavailable video': continue
            try:
                j=jellyfin.get_item(mapping,ident)
                v.update(title=j.get('Name') or 'Untitled',summary=j.get('Overview') or '',
                         rating=j.get('OfficialRating') or None,topics=j.get('Genres') or [],approved=ident in approved_jellyfin,
                         thumbnail=('/api/jellyfin/artwork/'+profile+'/'+ident) if (j.get('ImageTags') or {}).get('Primary') else '',series=j.get('SeriesName'))
            except jellyfin.JellyfinError: jellyfin_error=True
    result=sorted(items.values(),key=lambda v:-(v['lastAt'] or 0))
    topics={}
    for v in result:
        for name in {x.strip() for x in v['topics'] if isinstance(x,str) and x.strip()}:
            topics[name]=topics.get(name,0)+1
    return {'days':days,'generatedAt':now,'items':result,'events':events,
            'summary':{'titles':len(result),'measuredSeconds':measured_total,
                       'recordedSessions':len(recorded_sessions),'unrated':sum(not x['rating'] for x in result),
                       'flagged':sum(x['review']['status'] in ('discuss','avoid') for x in result),
                       'topics':[{'name':name,'titles':count} for name,count in sorted(topics.items(),key=lambda x:(-x[1],x[0].casefold()))[:6]]},
            'coverage':{'jellyfinAvailable':jellyfin_available,'jellyfinError':jellyfin_error,
                        'note':'Watch time is measured during supported playback in the updated Kidoo TV app and for local videos played in the Kidoo browser. Time is grouped by minute; delayed reporting can shift it slightly. External apps are not timed. Earlier Jellyfin entries show their latest known state, not exact watch time.'}}
