const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {unreviewed:'Not reviewed',reviewed:'Reviewed',discuss:'Discuss together',avoid:'Not suitable'};
let profile = localStorage.getItem('kidoo-home-profile') || '';
let days = 30, data = null, requestNumber = 0, protectedMode = false;
let libraryStart = 0, libraryTotal = 0, libraryTerm = '', libraryRequest = 0;
let promotionSet = new Set();

async function api(path, body) {
  const response=await fetch(path, body===undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if (!response.ok) throw new Error((await response.json().catch(()=>({}))).error || 'The request could not be completed.');
  return response.json();
}
const promotionKey=(kind,id)=>`${kind}:${id}`;
async function loadPromotion() {
  const selected=profile;
  const data=await api(`/api/promotion/preferences/${encodeURIComponent(selected)}`);
  if(selected===profile) promotionSet=new Set(data.items.map(item=>promotionKey(item.kind,item.itemId)));
}
function showLessControl(kind,id,title,selected) {
  const label=document.createElement('label'), checkbox=document.createElement('input');
  label.className='library-approval promotion-control';checkbox.type='checkbox';
  checkbox.dataset.promotionKey=promotionKey(kind,id);
  checkbox.checked=promotionSet.has(promotionKey(kind,id));
  checkbox.setAttribute('aria-label',`Show less: ${title}`);
  label.append(checkbox,document.createTextNode(' Show less'));
  checkbox.addEventListener('change',async()=>{
    const wanted=checkbox.checked;checkbox.disabled=true;
    try {
      await api('/api/promotion/preferences',{profileId:selected,kind,itemId:String(id),showLess:wanted});
      if(selected===profile) {
        if(wanted)promotionSet.add(promotionKey(kind,id));else promotionSet.delete(promotionKey(kind,id));
        document.querySelectorAll('.promotion-control input').forEach(input=>{if(input.dataset.promotionKey===promotionKey(kind,id))input.checked=wanted});
        $('#library-status').textContent='Preference saved. Approved content remains available.';
        $('#approval-status').textContent='Preference saved. Approved content remains available.';
      }
    } catch(error) {checkbox.checked=!wanted;$('#library-status').textContent=error.message;$('#approval-status').textContent=error.message}
    finally {checkbox.disabled=false}
  });
  return label;
}
function duration(value) {
  if (!Number.isFinite(value) || value<=0) return '—';
  if (value<60) return `${Math.max(1,Math.round(value))} sec`;
  const minutes=Math.max(1,Math.round(value/60)), hours=Math.floor(minutes/60), rest=minutes%60;
  return hours ? `${hours} hr${hours===1?'':'s'}${rest?` ${rest} min`:''}` : `${minutes} min`;
}
function position(value) {const seconds=Math.max(0,Math.floor(value||0)),hours=Math.floor(seconds/3600),minutes=Math.floor((seconds%3600)/60),rest=seconds%60;return hours?`${hours}:${String(minutes).padStart(2,'0')}:${String(rest).padStart(2,'0')}`:`${minutes}:${String(rest).padStart(2,'0')}`}
function safeCover(item) {
  return item.thumbnail?.startsWith('/') && !item.thumbnail.startsWith('//') ? item.thumbnail : '';
}
function date(value) {
  if (!value) return 'Date not recorded';
  return new Intl.DateTimeFormat(undefined,{day:'numeric',month:'short',year:'numeric'}).format(new Date(value*1000));
}
function renderChart(events) {
  const today=new Date(), buckets=[];
  for(let i=6;i>=0;i--) {
    const d=new Date(today.getFullYear(),today.getMonth(),today.getDate()-i);
    buckets.push({key:`${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`,label:new Intl.DateTimeFormat(undefined,{weekday:'short'}).format(d),seconds:0});
  }
  for(const event of events) {
    const d=new Date(event.at*1000), key=`${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
    const bucket=buckets.find(x=>x.key===key);if(bucket)bucket.seconds+=event.seconds;
  }
  const max=Math.max(...buckets.map(x=>x.seconds),60);
  $('#chart').innerHTML=buckets.map(x=>`<div class="chart-col"><div class="chart-bar-wrap"><div class="chart-bar" style="height:${Math.max(2,Math.round(x.seconds/max*100))}%" role="img" aria-label="${esc(x.label)}: ${esc(x.seconds?duration(x.seconds):'no measured playback')}"></div></div><small>${esc(x.label)}</small></div>`).join('');
}
function row(item) {
  const review=item.review || {status:'unreviewed',note:''};
  const image=safeCover(item);
  const cover=image ? `<img class="cover" src="${esc(image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : '<div class="cover-placeholder" aria-hidden="true">▷</div>';
  const details=[item.series,item.source].filter(Boolean).join(' · ');
  const watch=item.measuredSeconds?duration(item.measuredSeconds):'Time not measured';
  const context=[item.rating || 'No age rating',item.approved===true?'Approved in Kidoo':item.approved===false?'Not currently approved':'Approval unknown',...item.topics.slice(0,3)];
  const signals=[];
  if(item.completed)signals.push('Marked watched');else if(item.position>0)signals.push(`Stopped at ${position(item.position)}`);
  if(item.fullViews>1)signals.push(`${item.fullViews} long viewing sessions`);
  if(item.playCount)signals.push(`${item.playCount} Jellyfin play${item.playCount===1?'':'s'}`);
  const statuses=Object.entries(labels).map(([value,label])=>`<option value="${value}" ${review.status===value?'selected':''}>${label}</option>`).join('');
  return `<details class="activity-row" data-kind="${esc(item.kind)}" data-id="${esc(item.id)}"><summary>${cover}<div><div class="title">${esc(item.title)}</div><div class="sub">${esc(details)}</div></div><div class="row-date"><div class="sub">LAST ACTIVITY</div><strong>${esc(date(item.lastAt))}</strong></div><div class="row-side"><strong>${esc(watch)}</strong><div class="sub">${esc(labels[review.status] || labels.unreviewed)}</div></div><span class="row-arrow" aria-hidden="true">↗</span></summary><div class="row-detail"><p class="description">${esc(item.summary || 'No description has been added yet.')}</p><div class="facts">${context.map((x,i)=>`<span class="pill ${i===0&&!item.rating?'warn':''}">${esc(x)}</span>`).join('')}${item.duration?`<span class="pill">Length: ${esc(duration(item.duration))}</span>`:''}${signals.map(x=>`<span class="pill">${esc(x)}</span>`).join('')}</div>${item.reviewNotes?`<p class="footnote">Research note: ${esc(item.reviewNotes)}</p>`:''}<form class="review-form"><label>Your review<select name="status">${statuses}</select></label><label>Parent note<textarea name="note" maxlength="1000" placeholder="What would you like to remember?">${esc(review.note)}</textarea></label><button class="outline" type="submit">Save review</button><span class="review-status" role="status"></span></form><p class="footnote">Review labels and notes do not change what a child can play. Change the content approval separately to restrict it.</p></div></details>`;
}
function render() {
  $('#time-total').textContent=data.summary.recordedSessions?duration(data.summary.measuredSeconds):'—';
  $('#title-total').textContent=data.summary.titles;$('#session-total').textContent=data.summary.recordedSessions;$('#unrated-total').textContent=data.summary.unrated;
  $('#review-summary').innerHTML=`<span class="pill warn">${data.summary.unrated} without an age rating</span><span class="pill">${data.summary.flagged} marked for a closer look</span>`;
  $('#topics-summary').innerHTML=data.summary.topics.length?data.summary.topics.map(x=>`<span class="pill">${esc(x.name)} · ${x.titles}</span>`).join(''):'<span class="sub">No topics available yet.</span>';
  renderChart(data.events);
  const items=[...data.items], order=$('#sort').value;
  if(order==='time')items.sort((a,b)=>(b.measuredSeconds||0)-(a.measuredSeconds||0));
  if(order==='review')items.sort((a,b)=>Number(['discuss','avoid'].includes(b.review.status))-Number(['discuss','avoid'].includes(a.review.status)) || Number(!b.rating)-Number(!a.rating) || (b.lastAt||0)-(a.lastAt||0));
  $('#list').innerHTML=items.map(row).join('');$('#empty').hidden=items.length>0;
  $('#coverage').textContent=data.coverage.note+(data.coverage.jellyfinError?' Jellyfin is currently unavailable; some titles may be missing.':!data.coverage.jellyfinAvailable?' Jellyfin is not connected to this profile.':'');
}
async function refresh() {
  const selected=profile, period=days, id=++requestNumber;
  $('#loading').hidden=false;$('#error').hidden=true;$('#dashboard-content').hidden=true;
  try {
    const value=await api(`/api/dashboard/${encodeURIComponent(selected)}?days=${period}`);
    if(id!==requestNumber)return;data=value;render();$('#dashboard-content').hidden=false;
  } catch(error) {if(id!==requestNumber)return;$('#error').textContent=error.message;$('#error').hidden=false;}
  finally {if(id===requestNumber)$('#loading').hidden=true;}
}
async function start() {
  const session=await api('/api/session');$('#login').hidden=session.authenticated;$('#dashboard').hidden=!session.authenticated;
  protectedMode=Boolean(session.protectedMode);
  $('#login-intro').textContent=session.protectedMode?'Enter your administrator name and parent PIN.':'Enter your Kidoo server password to see your family’s viewing activity.';
  $('#notes-privacy').textContent=session.protectedMode?'Notes are stored on this server and accessible to signed-in parents.':'Notes are stored on this server and can be read by anyone with the shared login.';
  $('#security-coverage').textContent=session.protectedMode?'The parent dashboard requires an administrator PIN. Paired devices are restricted to their assigned profile.':'Kidoo currently uses a shared server login. This page is for parents, but it is not protected by a separate parent PIN.';
  $('#login-form').hidden=Boolean(session.protectedMode);
  $('#admin-login-form').hidden=location.protocol!=='https:';
  if(!session.authenticated)return;
  const profiles=await api('/api/profiles');if(!profiles.some(p=>p.id===profile))profile=profiles[0]?.id || '';
  const select=$('#profile');select.replaceChildren(...profiles.map(p=>{const o=document.createElement('option');o.value=p.id;o.textContent=p.name;return o}));select.value=profile;
  if(profile){await loadPromotion();await Promise.all([refresh(),loadLibrary(),loadLimits(),loadYourTv()]);}
  await loadSecurity();
}
$('#login-form').addEventListener('submit',async event=>{event.preventDefault();try{await api('/api/login',{password:$('#password').value});$('#password').value='';$('#login-error').textContent='';await start()}catch(error){$('#login-error').textContent=error.message}});
$('#admin-login-form').hidden=location.protocol!=='https:';
$('#admin-login-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.currentTarget,error=form.querySelector('[role=alert]');
  try {await api('/api/security/admin-login',{name:form.elements.name.value,pin:form.elements.pin.value});form.reset();error.textContent='';await start()}
  catch(problem){error.textContent=problem.message}
});
$('#profile').addEventListener('change',async event=>{profile=event.target.value;localStorage.setItem('kidoo-home-profile',profile);libraryStart=0;promotionSet=new Set();try{await loadPromotion();refresh();loadLibrary();loadSecurity();loadLimits();loadYourTv()}catch(error){$('#library-status').textContent=error.message}});
document.querySelectorAll('[data-days]').forEach(button=>button.addEventListener('click',()=>{days=Number(button.dataset.days);document.querySelectorAll('[data-days]').forEach(x=>x.setAttribute('aria-pressed',String(x===button)));refresh()}));
$('#refresh').addEventListener('click',refresh);$('#sort').addEventListener('change',()=>{if(data)render()});
$('#list').addEventListener('submit',async event=>{event.preventDefault();const form=event.target,row=form.closest('.activity-row'), status=form.querySelector('.review-status');if(!row)return;const selected=profile, values=new FormData(form);status.textContent='Saving…';try{await api('/api/dashboard/review',{profileId:selected,kind:row.dataset.kind,itemId:row.dataset.id,status:values.get('status'),note:values.get('note')});if(selected!==profile)return;const item=data.items.find(x=>x.kind===row.dataset.kind&&x.id===row.dataset.id);if(item)item.review={status:values.get('status'),note:values.get('note')};status.textContent='Saved on this server.';row.querySelector('.row-side .sub').textContent=labels[values.get('status')];data.summary.flagged=data.items.filter(x=>['discuss','avoid'].includes(x.review.status)).length;$('#review-summary').innerHTML=`<span class="pill warn">${data.summary.unrated} without an age rating</span><span class="pill">${data.summary.flagged} marked for a closer look</span>`}catch(error){status.textContent='Couldn’t save. Please try again.'}});
$('#list').addEventListener('error',event=>{if(event.target instanceof HTMLImageElement){const fallback=document.createElement('div');fallback.className='cover-placeholder';fallback.setAttribute('aria-hidden','true');fallback.textContent='▷';event.target.replaceWith(fallback)}},true);

$('#local-editor').addEventListener('toggle', async event=>{
  if(!event.currentTarget.open)return;
  const list=$('#local-list'), status=$('#local-status');status.textContent='Loading local videos…';list.replaceChildren();
  try {
    const result=await api('/api/local/catalogue');
    status.textContent=result.items.length?`${result.items.length} local ${result.items.length===1?'video':'videos'} found.`:'No local videos found in your media folder.';
    for(const item of result.items) {
      const m=item.metadata || {}, details=document.createElement('details'), summary=document.createElement('summary'), form=document.createElement('form');
      details.className='local-item'; details.dataset.search=`${m.title || ''} ${item.filename}`.toLocaleLowerCase();summary.textContent=m.title || item.filename;form.className='local-form';
      form.innerHTML=`<p class="footnote">File: ${esc(item.filename)}</p><label>Title<input name="title" maxlength="200" value="${esc(m.title || '')}" placeholder="${esc(item.filename)}"></label>
        <label>Type<select name="mediaType"><option value="Other">Other video</option><option value="Movie">Movie</option><option value="Episode">Series episode</option></select></label>
        <label>Series name<input name="seriesName" maxlength="200" value="${esc(m.seriesName || '')}"></label>
        <label>Season number<input name="seasonNumber" type="number" min="0" max="9999" value="${esc(m.seasonNumber ?? '')}"></label>
        <label>Episode number<input name="episodeNumber" type="number" min="0" max="9999" value="${esc(m.episodeNumber ?? '')}"></label>
        <label>Description<textarea name="summary" maxlength="2000">${esc(m.summary || '')}</textarea></label>
        <label>Topics, separated by commas<input name="tags" value="${esc((m.tags || []).join(', '))}" placeholder="animals, nature"></label>
        <button class="outline" type="submit">Save details</button><span class="review-status" role="status"></span>`;
      form.elements.mediaType.value=m.mediaType || 'Other';
      form.addEventListener('submit',async e=>{
        e.preventDefault();const state=form.querySelector('[role=status]');state.textContent='Saving…';
        const number=name=>form.elements[name].value===''?null:Number(form.elements[name].value);
        const metadata={title:form.elements.title.value,summary:form.elements.summary.value,mediaType:form.elements.mediaType.value,seriesName:form.elements.seriesName.value,seasonNumber:number('seasonNumber'),episodeNumber:number('episodeNumber'),tags:form.elements.tags.value.split(',').map(x=>x.trim()).filter(Boolean)};
        try {await api('/api/local/metadata',{itemId:item.id,metadata});summary.textContent=metadata.title.trim() || item.filename;details.dataset.search=`${metadata.title} ${item.filename}`.toLocaleLowerCase();state.textContent='Saved for all profiles.';}
        catch(error){state.textContent=error.message}
      });
      details.append(summary,form);details.hidden=!details.dataset.search.includes($('#local-search').value.trim().toLocaleLowerCase());list.append(details);
    }
  } catch(error){status.textContent=error.message}
});
$('#local-search').addEventListener('input',event=>{
  const query=event.target.value.trim().toLocaleLowerCase();
  document.querySelectorAll('#local-list .local-item').forEach(item=>{item.hidden=!item.dataset.search.includes(query)});
});

async function loadLibrary() {
  const selected=profile, request=++libraryRequest, start=libraryStart;
  const list=$('#library-list');list.replaceChildren();$('#library-status').textContent='Loading library…';$('#library-page').textContent='';
  $('#library-prev').disabled=true;$('#library-next').disabled=true;
  try {
    const query=new URLSearchParams({start:String(start),limit:'24',search:libraryTerm});
    const result=await api(`/api/jellyfin/catalogue/${encodeURIComponent(selected)}?${query}`);
    if(request!==libraryRequest||selected!==profile)return;
    libraryTotal=result.total;$('#library-status').textContent=libraryTotal?`${libraryTotal} ${libraryTotal===1?'title':'titles'} found.`:'No titles found. Try a different search.';
    const seriesGroups=new Map();
    for(const item of result.items) {
      const id=String(item.id).replace(/^jellyfin:/,'');
      const row=document.createElement('article');row.className='library-item';
      const img=document.createElement('img');img.className='library-cover';img.alt='';img.loading='lazy';
      if(item.hasArtwork)img.src=`/api/jellyfin/artwork/${encodeURIComponent(selected)}/${encodeURIComponent(id)}`;
      const content=document.createElement('div'), title=document.createElement('h3'), detail=document.createElement('p');
      title.textContent=item.title;detail.textContent=[item.series,item.year,item.rating,duration(item.duration)].filter(Boolean).join(' · ');
      const description=document.createElement('p');description.className='library-description';description.textContent=item.overview||'No description available.';
      const label=document.createElement('label'), checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=Boolean(item.approved);
      label.className='library-approval';label.append(checkbox,document.createTextNode(protectedMode?' Available to this profile':' Available to the family'));
      checkbox.addEventListener('change',async()=>{
        const wanted=checkbox.checked;checkbox.disabled=true;
        try {
          await api(protectedMode?'/api/security/approval':'/api/jellyfin/approval',protectedMode?{profileId:selected,kind:'jellyfin',itemId:id,approved:wanted}:{profileId:selected,itemId:id,approved:wanted});
          if(selected===profile)$('#library-status').textContent=protectedMode?'Saved for this profile.':'Saved for the family library.';
        } catch(error) {checkbox.checked=!wanted;$('#library-status').textContent=error.message}
        finally {checkbox.disabled=false}
      });
      content.append(title,detail,description,label,showLessControl('jellyfin',id,item.title,selected));row.append(img,content);
      if(item.type==='Episode' && item.series) {
        const key=item.seriesId || item.series;
        if(!seriesGroups.has(key)) {
          const group=document.createElement('details'), summary=document.createElement('summary'), children=document.createElement('div');
          group.className='library-series';summary.textContent=`${item.series} — episodes on this page`;group.append(summary);
          if(item.seriesId)group.append(showLessControl('series',String(item.seriesId),item.series,selected));
          group.append(children);list.append(group);seriesGroups.set(key,children);
        }
        seriesGroups.get(key).append(row);
      } else list.append(row);
    }
    $('#library-page').textContent=libraryTotal?`${start+1}–${Math.min(start+result.items.length,libraryTotal)} of ${libraryTotal}`:'';
    $('#library-prev').disabled=start===0;$('#library-next').disabled=start+result.items.length>=libraryTotal;
  } catch(error) {if(request===libraryRequest){$('#library-status').textContent=error.message;$('#library-page').textContent=''}}
}
$('#library-search-form').addEventListener('submit',event=>{event.preventDefault();libraryTerm=$('#library-search').value.trim();libraryStart=0;loadLibrary()});
$('#library-prev').addEventListener('click',()=>{libraryStart=Math.max(0,libraryStart-24);loadLibrary()});
$('#library-next').addEventListener('click',()=>{if(libraryStart+24<libraryTotal){libraryStart+=24;loadLibrary()}});
let approvalItems=[], approvalOther=[], approvalSet=new Set(), blockedSet=new Set(), securityRequest=0, approvalStart=0, approvalTotal=0, approvalRequest=0, approvalTimer;
const approvalKey=(kind,id)=>`${kind}:${id}`;
function showApprovals() {
  const term=$('#approval-search').value.trim().toLocaleLowerCase();
  const list=approvalItems.filter(v=>`${v.title} ${v.creator||''}`.toLocaleLowerCase().includes(term));
  const node=$('#approval-list');node.replaceChildren();
  for(const item of list) {
    const line=document.createElement('div');line.className='security-item';
    const label=document.createElement('label'), checkbox=document.createElement('input');checkbox.type='checkbox';
    checkbox.checked=approvalSet.has(approvalKey(item.kind,item.itemId));
    const text=document.createElement('span');text.textContent=`${item.title} · ${item.kind}`;
    label.append(checkbox);
    if(item.thumbnail) {const image=document.createElement('img');image.className='approval-cover';image.alt='';image.loading='lazy';image.referrerPolicy='no-referrer';image.src=item.thumbnail;label.append(image)}
    label.append(text);line.append(label);
    if(item.sourceUrl) {
      try {
        const url=new URL(item.sourceUrl);
        if(['http:','https:'].includes(url.protocol) && !url.username && !url.password) {
          const link=document.createElement('a');link.className='security-source';link.href=url.href;
          link.target='_blank';link.rel='noopener noreferrer';link.textContent=item.sourceLabel;
          link.setAttribute('aria-label',`${item.sourceLabel}: ${item.title} (opens in a new tab)`);line.append(link);
        }
      } catch (_) { /* A malformed source is not a link. */ }
    }
    node.append(line);
    checkbox.addEventListener('change',async()=>{
      const selected=profile, wanted=checkbox.checked;checkbox.disabled=true;
      try {await api('/api/security/approval',{profileId:selected,kind:item.kind,itemId:item.itemId,approved:wanted});
        if(selected!==profile)return;
        if(wanted){approvalSet.add(approvalKey(item.kind,item.itemId));blockedSet.delete(approvalKey(item.kind,item.itemId))}else{approvalSet.delete(approvalKey(item.kind,item.itemId));}
        $('#approval-status').textContent='Saved for this profile.';
      } catch(error) {checkbox.checked=!wanted;$('#approval-status').textContent=error.message;}
      finally {checkbox.disabled=false;}
    });
  }
  if(!list.length)$('#approval-status').textContent='No matching titles.';
}
async function loadSecurity() {
  const secure=location.protocol==='https:';
  $('#security-http').hidden=secure;$('#security-controls').hidden=!secure;
  if(!secure||!profile)return;
  const id=++securityRequest, selected=profile;
  try {
    const [devices,approvals,home]=await Promise.all([api('/api/security/devices'),api(`/api/security/approvals/${encodeURIComponent(selected)}`),api(`/api/tv/home/${encodeURIComponent(selected)}?view=parent&paged=1`)]);
    if(id!==securityRequest||selected!==profile)return;
    const node=$('#security-devices');node.replaceChildren();
    for(const device of devices.devices) {
      const row=document.createElement('div'), text=document.createElement('span'), button=document.createElement('button');
      row.className='security-item';text.textContent=`${device.label} · ${device.profileId}`;button.type='button';button.className='outline';button.textContent='Revoke';
      button.addEventListener('click',async()=>{button.disabled=true;try{await api('/api/security/revoke',{deviceId:device.id});await loadSecurity()}catch(error){$('#approval-status').textContent=error.message;button.disabled=false}});
      row.append(text,button);node.append(row);
    }
    if(!devices.devices.length)node.textContent='No devices paired yet.';
    approvalSet=new Set(approvals.approvals.map(a=>approvalKey(a.kind,a.itemId)));
    blockedSet=new Set((approvals.blocks||[]).map(a=>approvalKey(a.kind,a.itemId)));
    approvalOther=[...home.local.map(v=>({kind:'local',itemId:String(v.id),title:v.title})),...home.jellyfin.map(v=>({kind:'jellyfin',itemId:String(v.id),title:v.title,creator:v.creator})),];
    approvalStart=0;await loadApprovalsPage();
  } catch(error) {if(id===securityRequest)$('#approval-status').textContent=error.message;}
}
$('#pin-form').addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget,status=form.querySelector('[role=status]'),pin=form.elements.pin.value;try{await api('/api/security/pin',{pin});form.reset();await start();$('#login-error').textContent='PIN saved. Sign in again with your administrator name and PIN.'}catch(error){status.textContent=error.message}});
$('#pair-form').addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget,status=form.querySelector('[role=status]');try{const result=await api('/api/security/pairing',{profileId:profile,label:form.elements.label.value});$('#pair-code').textContent=`Enter this code on the TV within five minutes: ${result.code}`;form.reset();status.textContent='';}catch(error){status.textContent=error.message}});
$('#import-approvals').addEventListener('click',async()=>{const selected=profile;try{await api('/api/security/import-approvals',{profileId:selected});$('#import-status').textContent='Existing family approvals imported.';await loadSecurity()}catch(error){$('#import-status').textContent=error.message}});
async function loadApprovalsPage() {
  const term=$('#approval-search').value.trim().toLocaleLowerCase();
  const matching=approvalOther.filter(v=>`${v.title} ${v.creator||''}`.toLocaleLowerCase().includes(term));
  approvalTotal=matching.length;
  approvalItems=matching.slice(approvalStart,approvalStart+50);
  $('#approval-page').textContent=approvalTotal?`${approvalStart+1}–${Math.min(approvalStart+50,approvalTotal)} of ${approvalTotal} videos`:'';
  $('#approval-prev').disabled=approvalStart===0;$('#approval-next').disabled=approvalStart+50>=approvalTotal;
  $('#approval-status').textContent='';showApprovals();
}
$('#approval-search').addEventListener('input',()=>{clearTimeout(approvalTimer);approvalStart=0;approvalTimer=setTimeout(loadApprovalsPage,250)});
$('#approval-prev').addEventListener('click',()=>{approvalStart=Math.max(0,approvalStart-50);loadApprovalsPage()});
$('#approval-next').addEventListener('click',()=>{if(approvalStart+50<approvalTotal){approvalStart+=50;loadApprovalsPage()}});
$('#approval-list').addEventListener('error',event=>{if(event.target instanceof HTMLImageElement)event.target.remove()},true);


start().catch(()=>{$('#login').hidden=false;$('#login-error').textContent='Couldn’t connect to the home server.'});

const limitKeys=['total','movies','series','other'];
async function loadLimits() {
  const selected=profile;
  try {
    const result=await api('/api/viewing-limits/'+encodeURIComponent(selected));
    if(selected!==profile)return;
    const form=$('#limits-form');
    for(const key of limitKeys)form.elements[key].value=result.limits[key] ?? '';
    const usage=result.usedSeconds;
    $('#limits-usage').textContent=`Today: ${Math.ceil(usage.total/60)} min total · ${Math.ceil(usage.movies/60)} min movies · ${Math.ceil(usage.series/60)} min series · ${Math.ceil(usage.other/60)} min other. Server timezone: ${result.timezone}.` + (usage.unclassified ? ' Earlier unclassified viewing counts against each local category today.' : '');
    $('#limits-status').textContent='';
  } catch(error) { if(selected===profile)$('#limits-status').textContent='Could not load limits. '+error.message; }
}
$('#limits-form').addEventListener('submit',async event=>{
  event.preventDefault();const selected=profile,form=event.currentTarget;
  const limits=Object.fromEntries(limitKeys.map(key=>[key,form.elements[key].value===''?null:Number(form.elements[key].value)]));
  $('#limits-status').textContent='Saving…';
  try {await api('/api/viewing-limits',{profileId:selected,limits});if(selected===profile){await loadLimits();$('#limits-status').textContent='Limits saved.'}}
  catch(error){if(selected===profile)$('#limits-status').textContent=error.message}
});

async function loadYourTv() {
  const selected=profile;
  try {
    const data=await api('/api/your-tv/manage/'+encodeURIComponent(selected));
    if(selected!==profile)return;
    const root=$('#your-tv-list');root.replaceChildren();
    for(const channel of data.channels) {
      const row=document.createElement('div');row.className='security-item';
      const label=document.createElement('span');label.textContent=channel.name+' · '+(channel.enabled?'Visible':'Hidden')+' · '+channel.topics.join(', ');
      const edit=document.createElement('button');edit.type='button';edit.className='outline';edit.textContent='Edit';
      edit.addEventListener('click',()=>{
        const form=$('#your-tv-form');for(const key of ['id','name'])form.elements[key].value=channel[key];
        for(const key of ['topics','languages','sources'])form.elements[key].value=channel[key].join(', ');
        form.elements.enabled.checked=channel.enabled;form.parentElement.open=true;form.elements.name.focus();
      });
      const remove=document.createElement('button');remove.type='button';remove.className='outline';remove.textContent='Remove';
      remove.addEventListener('click',async()=>{if(!confirm('Remove this channel? Approved videos remain available.'))return;try{await api('/api/your-tv/delete',{profileId:selected,channelId:channel.id});if(selected===profile)await loadYourTv()}catch(error){$('#your-tv-status').textContent=error.message}});
      row.append(label,edit,remove);root.append(row);
    }
  } catch(error) {if(selected===profile)$('#your-tv-status').textContent=error.message}
}
$('#your-tv-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.currentTarget,selected=profile;
  const list=key=>form.elements[key].value.split(',').map(x=>x.trim()).filter(Boolean);
  const channel={id:form.elements.id.value.trim(),name:form.elements.name.value.trim(),enabled:form.elements.enabled.checked,topics:list('topics'),languages:list('languages'),sources:list('sources')};
  try {await api('/api/your-tv/save',{profileId:selected,channel});if(selected===profile){await loadYourTv();$('#your-tv-status').textContent='Channel saved.'}}
  catch(error){$('#your-tv-status').textContent=error.message}
});
$('#your-tv-reset').addEventListener('click',async()=>{
  if(!confirm('Restore the default channels for this profile? Your custom channels will be removed.'))return;
  const selected=profile;try{await api('/api/your-tv/reset',{profileId:selected});if(selected===profile){await loadYourTv();$('#your-tv-status').textContent='Defaults restored.'}}
  catch(error){$('#your-tv-status').textContent=error.message}
});
