const $ = selector => document.querySelector(selector);
const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let profile = localStorage.getItem('kidoo-home-profile') || 'family';
let view = 'parent', protectedMode = false;
let items = [], active = null, lastSaved = 0, saveQueue = Promise.resolve();
const player = $('#player');
let viewing=null, activityQueue=Promise.resolve();
function observeViewing() {
  if(!viewing)return;
  const now=performance.now(), position=player.currentTime;
  if(viewing.observedAt && viewing.playing && !player.seeking && Number.isFinite(position)) {
    const elapsed=(now-viewing.observedAt)/1000, advanced=position-viewing.position;
    if(elapsed>0 && elapsed<10 && advanced>0 && advanced<elapsed+1) viewing.watched+=Math.min(elapsed,advanced);
  }
  viewing.observedAt=now;viewing.position=Number.isFinite(position)?position:0;viewing.playing=!player.paused && !player.seeking;
}
function reportViewing(finished=false) {
  if(!viewing)return Promise.resolve();
  observeViewing();const record=viewing;
  const event={profileId:record.profile,sessionId:record.session,sequence:++record.sequence,watchedSeconds:record.watched,duration:Number.isFinite(player.duration)?player.duration:0,position:record.position,finished};
  activityQueue=activityQueue.catch(()=>{}).then(async()=>{
    const result=await api('/api/activity/event',event);record.enabled=result.limit.enabled;
    if(result.limit.blocked && viewing===record){record.blocked=true;player.pause();$('#player-message').textContent='Your viewing time is finished for today. Ask a grown-up for help.';}
  }).catch(()=>{if(record.enabled && viewing===record){record.blocked=true;player.pause();$('#player-message').textContent='Kidoo needs to reconnect before watching can continue.';}});
  return activityQueue;
}
setInterval(()=>{if(viewing&&!player.paused)reportViewing()},2000);
player.addEventListener('play',()=>{if(viewing?.blocked){player.pause();return}if(viewing){viewing.observedAt=performance.now();viewing.position=player.currentTime;viewing.playing=true}});
player.addEventListener('seeking',()=>{if(viewing){viewing.playing=false;viewing.observedAt=0}});
player.addEventListener('seeked',()=>{if(viewing){viewing.observedAt=performance.now();viewing.position=player.currentTime;viewing.playing=!player.paused}});
async function api(path, data) {
  const res = await fetch(path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Kidoo-Browser':'1'},body:JSON.stringify(data)});
  if (!res.ok) throw new Error((await res.json().catch(()=>({}))).error || `Request failed (${res.status})`);
  return res.json();
}
function say(message) { $('#status').textContent = message; setTimeout(()=>{$('#status').textContent=''},3500); }
function stamp(seconds) { const n=Math.floor(seconds||0);return `${Math.floor(n/60)}:${String(n%60).padStart(2,'0')}`; }
async function profiles() {
  const list=await api('/api/profiles'); if(!list.some(p=>p.id===profile)) profile=list[0].id;
  $('#profile').innerHTML=list.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join(''); $('#profile').value=profile;
}
function tile(item) {
  const p=item.progress, pct=p?.duration ? Math.min(100,Math.round(p.position/p.duration*100)):0;
  const status=p?.completed ? 'WATCHED' : p?.position>0 ? `${stamp(p.position)} / ${stamp(p.duration)}` : 'NEW';
  return `<article class="tile"><button class="play-tile" data-play="${item.id}" aria-label="${p?.position&&!p.completed?'Resume':'Play'} ${esc(item.title)}"><div class="tile-art"><span class="tile-symbol">▷</span><span class="tile-badge">${status}</span></div><h3>${esc(item.title)}</h3><p>${item.approved?'✓ Approved for family':'Parent review pending'}</p>${p&&!p.completed&&pct?`<div class="bar"><span style="width:${pct}%"></span></div>`:''}</button>${view==='parent'?`<div class="tile-actions"><button class="quiet" data-approve="${item.id}">${item.approved?'Remove approval':'Approve for family'}</button></div>`:''}</article>`;
}
async function refresh() {
  $('#dashboard-link').hidden=view!=='parent';
  items=await api('/api/library/'+encodeURIComponent(profile));
  const visible = view==='family' ? items.filter(item=>item.approved) : items;
  const partial=visible.filter(x=>x.progress?.position>0&&!x.progress.completed).sort((a,b)=>b.progress.updatedAt-a.progress.updatedAt);
  const watched=visible.filter(x=>x.progress).sort((a,b)=>b.progress.updatedAt-a.progress.updatedAt);
  $('#continue-section').hidden=!partial.length; $('#continue-list').innerHTML=partial.map(tile).join('');
  $('#library').innerHTML=visible.map(tile).join(''); $('#empty').hidden=visible.length>0; $('#empty').textContent = view==='family' ? 'No approved videos yet. Open Parent view to choose videos for your family.' : 'Your collection is waiting. Add an MP4, WebM, MOV or MKV file to your media folder, then refresh.'; $('#count').textContent=`${visible.length} ${visible.length===1?'video':'videos'}`;
  $('#history-section').hidden=!watched.length; $('#history').innerHTML=watched.map(x=>`<div class="history-row"><strong>${esc(x.title)}</strong><span>${x.progress.completed?'Watched':`Stopped at ${stamp(x.progress.position)}`} · ${new Date(x.progress.updatedAt*1000).toLocaleDateString()}</span></div>`).join('');
}
async function save(completed=false) {
  if(!active) return;
  const snapshot={profileId:profile,mediaId:active.id,position:Number.isFinite(player.currentTime)?player.currentTime:0,duration:Number.isFinite(player.duration)?player.duration:0,completed};
  saveQueue = saveQueue.catch(()=>{}).then(() => api('/api/progress',snapshot));
  try {await saveQueue; lastSaved=Date.now();} catch {$('#player-message').textContent='Couldn’t save progress. Check the connection to your home server.';}
}
async function play(id) {
  if(active) {observeViewing();player.pause();await reportViewing();await save();viewing=null;}
  active=items.find(x=>x.id===id); if(!active)return;
  $('#player-section').hidden=false; $('#player-title').textContent=active.title; $('#player-message').textContent='';
  try {
    const result=await api('/api/activity/start',{profileId:profile,kind:'local',itemId:id});
    viewing={profile,session:result.sessionId,sequence:0,watched:0,position:0,observedAt:0,playing:false,enabled:result.limit.enabled,blocked:false};
  } catch(error) {$('#player-message').textContent=error.message;active=null;return;}
  player.src='/media/'+encodeURIComponent(id)+'?profileId='+encodeURIComponent(profile)+'&activity='+encodeURIComponent(viewing.session);
  const position=active.progress&&!active.progress.completed ? active.progress.position : 0;
  player.addEventListener('loadedmetadata',()=>{if(position>0&&Number.isFinite(player.duration)){player.currentTime=Math.min(position,Math.max(0,player.duration-1));$('#player-message').textContent=`Resuming at ${stamp(position)}.`;}},{once:true});
  $('#player-section').scrollIntoView({behavior:'smooth',block:'start'});
  try {await player.play()}catch {$('#player-message').textContent='Press play to begin. This device may not support every video format.';}
}
player.addEventListener('timeupdate',()=>{if(Date.now()-lastSaved>=5000&&!player.paused){lastSaved=Date.now();save()}});
player.addEventListener('pause',()=>{if(viewing){observeViewing();viewing.playing=false;reportViewing()}if(active&&!player.ended)save()});
player.addEventListener('ended',async()=>{await reportViewing(true);await save(true);await refresh()});
player.addEventListener('error',()=>{$('#player-message').textContent='This video could not play on this device. Try an MP4 with H.264 video and AAC audio.'});
$('#close-player').addEventListener('click',async()=>{player.pause();await save();player.removeAttribute('src');player.load();active=null;viewing=null;$('#player-section').hidden=true;await refresh()});
$('#restart').addEventListener('click',async()=>{player.currentTime=0;await save(false);player.play().catch(()=>{})});
$('#mark-watched').addEventListener('click',async()=>{player.pause();await save(true);active=null;viewing=null;player.removeAttribute('src');player.load();$('#player-section').hidden=true;await refresh();say('Marked as watched.')});
$('#profile').addEventListener('change',async e=>{if(active){player.pause();await save();active=null;viewing=null;player.removeAttribute('src');player.load();$('#player-section').hidden=true}profile=e.target.value;localStorage.setItem('kidoo-home-profile',profile);await refresh()});
document.addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.view){if(b.dataset.view==='family' && active && !active.approved){player.pause();await save();active=null;viewing=null;player.removeAttribute('src');player.load();$('#player-section').hidden=true;}view=b.dataset.view;document.querySelectorAll('[data-view]').forEach(x=>x.setAttribute('aria-pressed',x.dataset.view===view));await refresh();return;}if(b.dataset.play)await play(b.dataset.play);if(b.dataset.approve){const item=items.find(x=>x.id===b.dataset.approve);await api(protectedMode?'/api/security/approval':'/api/approval',protectedMode?{profileId:profile,kind:'local',itemId:item.id,approved:!item.approved}:{mediaId:item.id,approved:!item.approved});await refresh()}});
$('#profile-form').addEventListener('submit',async e=>{e.preventDefault();try{const p=await api('/api/profiles',{name:$('#new-profile').value});profile=p.id;localStorage.setItem('kidoo-home-profile',profile);$('#new-profile').value='';await profiles();await refresh();say('Profile added.')}catch(err){say(err.message)}});
$('#login-form').addEventListener('submit',async e=>{e.preventDefault();try{await api('/api/login',{password:$('#password').value});$('#password').value='';await start()}catch(err){$('#login-error').textContent=err.message}});
async function start(){const s=await api('/api/session');if(s.browserChild){location.replace('/child');return}protectedMode=Boolean(s.protectedMode);$('#login-intro').textContent=protectedMode?'Enter your administrator name and parent PIN.':"Enter your home server password to open your family's collection.";$('#login-form').hidden=protectedMode;$('#admin-login-form').hidden=!protectedMode;$('#login').hidden=s.authenticated;$('#app').hidden=!s.authenticated;$('.profile-wrap').hidden=!s.authenticated;$('#dashboard-link').hidden=!s.authenticated||view!=='parent';if(s.authenticated){await profiles();await refresh()}}
$('#admin-login-form').addEventListener('submit',async event=>{event.preventDefault();const form=event.currentTarget,error=form.querySelector('[role=alert]');try{await api('/api/security/admin-login',{name:form.elements.name.value,pin:form.elements.pin.value});form.reset();error.textContent='';await start()}catch(problem){error.textContent=problem.message}});
start().catch(()=>{$('#login').hidden=false;$('#login-error').textContent='Couldn’t connect to the home server.'});
