const $=selector=>document.querySelector(selector);
const player=$('#player');
let profile='',home=null,active=null,queue=Promise.resolve(),suggestionSession=Math.floor(Date.now()/1000);

async function api(path,body,options={}) {
  const response=await fetch(path,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','X-Kidoo-Browser':'1'},body:JSON.stringify(body),keepalive:Boolean(options.keepalive)});
  if(!response.ok){const detail=await response.json().catch(()=>({}));const error=new Error(detail.error||'Kidoo could not complete that request.');error.status=response.status;throw error}
  return response.json();
}
function message(text){$('#notice').textContent=text}
function setScreen(name){$('#pairing').hidden=name!=='pair';$('#unlock').hidden=name!=='unlock';$('#library').hidden=name!=='library';$('#search-wrap').hidden=name!=='library';$('#grownups').hidden=name!=='library'}
function safePath(value){try{if(typeof value!=='string'||!value.startsWith('/')||value.startsWith('//'))return '';const parsed=new URL(value,location.origin);return parsed.origin===location.origin?parsed.pathname+parsed.search:''}catch(_){return ''}}
function card(item){
  const button=document.createElement('button');button.type='button';button.className='video-card';
  const image=safePath(item.thumbnail);
  if(image){const img=document.createElement('img');img.src=image;img.alt='';img.loading='lazy';img.addEventListener('error',()=>{const fallback=document.createElement('span');fallback.className='placeholder';fallback.setAttribute('aria-hidden','true');fallback.textContent='▶';img.replaceWith(fallback)});button.append(img)}
  else {const fallback=document.createElement('span');fallback.className='placeholder';fallback.setAttribute('aria-hidden','true');fallback.textContent='▶';button.append(fallback)}
  const title=document.createElement('strong');title.textContent=item.title;button.append(title);
  const detail=document.createElement('small');if(item.kind==='series')detail.textContent=`${(item.episodes||[]).length} episodes`;else if(item.progress?.position&&!item.progress.completed)detail.textContent='Continue watching';else detail.textContent=item.creator==='Family library'?'':item.creator||'';button.append(detail);
  button.addEventListener('click',()=>item.kind==='series'?showEpisodes(item):play(item));return button;
}
function addShelf(title,items){if(!items.length)return;const section=document.createElement('section');section.className='shelf';const heading=document.createElement('h2');heading.textContent=title;const cards=document.createElement('div');cards.className='cards';items.forEach(item=>cards.append(card(item)));section.append(heading,cards);$('#shelves').append(section)}
function render(){
  $('#shelves').replaceChildren();const term=$('#search').value.trim().toLocaleLowerCase();
  const match=item=>!term||[item.title,item.creator,item.summary].some(v=>String(v||'').toLocaleLowerCase().includes(term));
  const seen=new Set();
  function unique(items){return items.filter(item=>{if(!match(item)||seen.has(item.key))return false;seen.add(item.key);return true})}
  for(const row of home?.rows||[]){if(['continue','recommended','favourites'].includes(row.id))addShelf(row.title,unique(row.items||[]))}
  for(const [key,title] of [['movies','Movies'],['series','Series'],['other','More videos']])addShelf(title,unique(home?.sections?.[key]||[]));
  $('#empty').hidden=$('#shelves').childElementCount>0;$('#empty').textContent=term?'No videos match your search.':'No videos here yet. Ask a grown-up to choose some for you.';
}
function showEpisodes(series){$('#shelves').replaceChildren();const back=document.createElement('button');back.type='button';back.className='quiet';back.textContent='← Back';back.addEventListener('click',render);$('#shelves').append(back);addShelf(series.title,series.episodes||[]);$('#shelves').scrollIntoView({behavior:'smooth',block:'start'})}
async function loadHome(){home=await api(`/api/tv/home/${encodeURIComponent(profile)}?view=family&suggestionSession=${suggestionSession}`);render()}
function observe(record){
  if(!record||active!==record)return;
  const now=performance.now(),position=Number.isFinite(player.currentTime)?player.currentTime:0;
  if(record.observedAt&&record.playing&&!player.seeking){const elapsed=(now-record.observedAt)/1000,advanced=position-record.observedPosition;if(elapsed>0&&elapsed<10&&advanced>0&&advanced<elapsed+1)record.watched+=Math.min(elapsed,advanced)}
  record.observedAt=now;record.observedPosition=position;record.playing=!player.paused&&!player.seeking;
}
function report(record,finished=false,stop=false,keepalive=false){
  if(!record||record.stopped)return queue;
  observe(record);const position=Number.isFinite(player.currentTime)?Math.max(0,player.currentTime):0,duration=Number.isFinite(player.duration)?Math.max(0,player.duration):0;
  const activity={profileId:profile,sessionId:record.activity,sequence:++record.activitySequence,watchedSeconds:record.watched,duration,position,finished};
  const progress=record.item.kind==='local'?{profileId:profile,mediaId:String(record.item.id),position,duration,completed:finished}:{profileId:profile,sessionId:record.jellyfinSession,sequence:++record.jellyfinSequence,event:stop?'stop':'progress',position,paused:player.paused};
  if(stop)record.stopped=true;
  queue=queue.catch(()=>{}).then(async()=>{
    const result=await api('/api/activity/event',activity,{keepalive});record.limitEnabled=Boolean(result.limit.enabled);
    if(result.limit.blocked&&active===record){record.blocked=true;player.pause();$('#player-status').textContent='Your watching time is finished for today. Ask a grown-up for help.'}
    await api(record.item.kind==='local'?'/api/progress':'/api/jellyfin/event',progress,{keepalive});
  }).catch(error=>{
    if(active!==record)return;
    if(record.limitEnabled||[401,403,409,429].includes(error.status)){record.blocked=true;player.pause()}
    $('#player-status').textContent=error.status===409?'Watching progress changed on another device. Ask a grown-up for help.':'Kidoo could not save your place. Ask a grown-up to check the connection.';
  });
  return queue;
}
async function closePlayer(){const record=active;if(record){observe(record);player.pause();await report(record,false,true);active=null}player.removeAttribute('src');player.load();$('#player-section').hidden=true;await loadHome().catch(()=>message('Kidoo needs to reconnect.'))}
async function play(item){
  if(!['local','jellyfin'].includes(item.kind))return;
  if(active)await closePlayer();
  $('#player-section').hidden=false;$('#player-title').textContent=item.title;$('#player-status').textContent='Getting your video ready…';$('#player-section').scrollIntoView({behavior:'smooth',block:'start'});
  let started=null;
  try {
    if(item.kind==='jellyfin')started=await api('/api/jellyfin/start',{profileId:profile,itemId:String(item.id),transcode:false});
    const activity=await api('/api/activity/start',{profileId:profile,kind:item.kind,itemId:String(item.id),...(started?{playbackSession:started.sessionId}:{})});
    const record={item,activity:activity.sessionId,limitEnabled:Boolean(activity.limit.enabled),activitySequence:0,jellyfinSession:started?.sessionId||'',jellyfinSequence:0,watched:0,observedAt:0,observedPosition:0,playing:false,blocked:false,stopped:false};active=record;
    const url=started?safePath(started.streamUrl):`/media/${encodeURIComponent(item.id)}`;if(!url)throw new Error('Invalid playback address');
    player.src=`${url}${url.includes('?')?'&':'?'}profileId=${encodeURIComponent(profile)}&activity=${encodeURIComponent(record.activity)}`;
    const position=started?(started.completed?0:started.position):(item.progress&&!item.progress.completed?item.progress.position:0);
    player.addEventListener('loadedmetadata',()=>{if(active===record&&position>0&&Number.isFinite(player.duration))player.currentTime=Math.min(position,Math.max(0,player.duration-1))},{once:true});
    $('#favourite').textContent=item.favourite?'★ Remove from favourites':'☆ Add to favourites';$('#player-status').textContent='';
    try{await player.play()}catch(_){$('#player-status').textContent='Press play to begin.'}
  } catch(error){
    if(started?.sessionId)api('/api/jellyfin/event',{profileId:profile,sessionId:started.sessionId,sequence:1,event:'stop',position:started.position||0,paused:true}).catch(()=>{});
    active=null;$('#player-status').textContent=error.status===429?'Your watching time is finished for today. Ask a grown-up for help.':'This video is not available. Ask a grown-up for help.';
  }
}
player.addEventListener('play',()=>{if(active?.blocked){player.pause();return}if(active){active.observedAt=performance.now();active.observedPosition=player.currentTime;active.playing=true}});
player.addEventListener('pause',()=>{if(active){observe(active);active.playing=false;report(active)}});
player.addEventListener('seeking',()=>{if(active){active.playing=false;active.observedAt=0}});
player.addEventListener('seeked',()=>{if(active){active.observedAt=performance.now();active.observedPosition=player.currentTime;active.playing=!player.paused}});
player.addEventListener('ended',async()=>{if(active){const record=active;await report(record,true,true);if(active===record){active=null;$('#player-status').textContent='All done!';await loadHome().catch(()=>{})}}});
player.addEventListener('error',()=>{if(active)$('#player-status').textContent='This video cannot play on this browser. Ask a grown-up for help.'});
setInterval(()=>{if(active&&!player.paused)report(active)},5000);
$('#close-player').addEventListener('click',()=>closePlayer());
$('#favourite').addEventListener('click',async()=>{if(!active)return;const item=active.item;try{await api('/api/tv/favourite',{profileId:profile,kind:item.kind,itemId:String(item.id),favourite:!item.favourite});item.favourite=!item.favourite;$('#favourite').textContent=item.favourite?'★ Remove from favourites':'☆ Add to favourites'}catch(_){$('#player-status').textContent='Could not save your favourite. Ask a grown-up for help.'}});
$('#search').addEventListener('input',render);
$('#grownups').addEventListener('click',async()=>{if(active)await closePlayer();setScreen('unlock');$('#pin').focus()});
$('#cancel-unlock').addEventListener('click',()=>setScreen('library'));
$('#unlock-form').addEventListener('submit',async event=>{event.preventDefault();const pin=$('#pin').value;$('#pin').value='';try{await api('/api/browser/unlock',{pin});location.assign('/dashboard')}catch(_){$('#unlock-error').textContent='PIN not accepted. Ask a grown-up to try again later.'}});
$('#pair-form').addEventListener('submit',async event=>{event.preventDefault();const code=$('#pair-code').value.trim();$('#pair-code').value='';try{await api('/api/browser/pair',{code});await start()}catch(_){$('#pair-error').textContent='That code did not work. Ask a grown-up for a new one.'}});
window.addEventListener('pagehide',()=>{if(active)report(active,false,true,true)});
async function start(){const session=await api('/api/session');if(!session.protectedMode||location.protocol!=='https:'){setScreen('pair');$('#pair-error').textContent='Ask a grown-up to set up protected HTTPS first.';return}if(!session.browserChild||!session.authenticated){setScreen('pair');return}if(session.parentUnlocked)await api('/api/browser/lock',{});profile=session.profileId;setScreen('library');await loadHome()}
start().catch(()=>{setScreen('pair');$('#pair-error').textContent='Kidoo needs to reconnect. Ask a grown-up for help.'});
