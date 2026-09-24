'use strict';
const $ = s => document.querySelector(s);
const escape = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const prefix = 'talkwithagent-';
let session = new URL(location.href).searchParams.get('session') || localStorage.getItem(prefix+'session') || localStorage.getItem('sidecar-session');
let state, selected, filter = 'open', locked = false, version = -1, toastTimer;
const time = v => new Date(v * 1000).toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'});
const kindName = {discussion:'讨论',question:'问题',suggestion:'建议',decision:'待决策'};
function draftKey(id=selected) { return prefix+'draft-'+session+'-'+id; }
function toast(text) {
  $('#toast').textContent = text; $('#toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 4500);
}
async function api(path, body) {
  const response = await fetch(path, { ...(body ? {method:'POST',headers:{'Content-Type':'application/json','X-TalkWithAgent':'1'},body:JSON.stringify(body)} : {}), signal:AbortSignal.timeout(10000)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '请求失败，请重试');
  return result;
}
function bind(id) {
  session=id; selected=localStorage.getItem(prefix+'topic-'+id); version=-1;
  $('#input').value=selected?(localStorage.getItem(draftKey())||''):'';
  $('#messages').replaceChildren();$('.workspace').classList.remove('detail-open');
  localStorage.setItem(prefix+'session',id);
  const url=new URL(location.href);url.searchParams.set('session',id);history.replaceState(null,'',url);
}
function selectTopic(id, openDetail=true) {
  if(selected) localStorage.setItem(draftKey(),$('#input').value);
  selected=id;localStorage.setItem(prefix+'topic-'+session,id);
  localStorage.setItem(prefix+'seen-'+session+'-'+id,'1');
  $('#input').value=localStorage.getItem(draftKey())||'';
  $('#messages').replaceChildren();
  if(openDetail) $('.workspace').classList.add('detail-open');
  renderTopics();renderDetail();
}
function render(next) {
  if(next.id!==session||next.version<version)return;
  state=next;$('#connection').textContent='已连接 · 本地保存';
  if(version===state.version)return;
  version=state.version;
  $('#task-name').textContent=state.title;
  $('#task-status').textContent={working:'执行中',waiting:'等待决定',completed:'已完成'}[state.status];
  const progress=state.events.filter(e=>e.kind==='progress'&&!e.archived).at(-1);
  $('#task-update').textContent=progress?.text||'尚无进展';
  $('#main-id').textContent=state.main_thread||'未绑定';
  $('#discussion-id').textContent=state.codex_thread||'首次思考时建立';
  const queued=state.jobs?.length>0;
  $('#agent-state').textContent=state.codex_available===false?'讨论 agent 未连接':state.busy?'agent 正在思考':queued?'正在准备回复':state.agent_error?'讨论暂时中断':state.auto_discuss===false?'主动话题已关闭':state.status==='completed'?'随时可以继续讨论':'正在关注任务';
  $('#agent-note').textContent=state.codex_available===false?'请安装 Codex CLI 并登录，然后重新启动服务。':state.agent_error||(state.busy?'你仍可以发起话题或补充想法。':state.auto_discuss===false?'你仍可以发起话题和继续讨论。':state.status==='completed'?'已有话题和讨论记录会保留。':'有值得讨论的新进展时，我会开启话题。');
  $('#agent-light').classList.toggle('busy',state.busy);
  $('#retry').hidden=!state.agent_error;
  const visible=state.cards.filter(c=>!c.archived);
  if(!selected || !visible.some(c=>c.id===selected)) {
    selected=visible.find(c=>c.status==='pending')?.id||visible.at(-1)?.id;
    $('#input').value=selected?(localStorage.getItem(draftKey())||''):'';
    $('#messages').replaceChildren();
  }
  renderTopics();renderDetail();
}
function renderTopics() {
  if(!state)return;
  const topics=state.cards.filter(c=>!c.archived);
  const open=topics.filter(c=>c.status!=='resolved');
  $('#topic-count').textContent=open.length;
  const shown=(filter==='open'?open:topics).slice().sort((a,b)=>(b.created||0)-(a.created||0));
  $('#topics').innerHTML=shown.length?shown.map(c=>{
    const unread=c.owner==='agent'&&!localStorage.getItem(prefix+'seen-'+session+'-'+c.id);
    const status=c.status==='resolved'?(c.delivery==='received'?'Codex 已读取':c.delivery?'等待 Codex 读取':'已结束'):c.status==='deferred'?'稍后继续':c.kind==='discussion'?'讨论中':'等你回应';
    return '<button class="topic-item '+(selected===c.id?'selected':'')+'" data-topic="'+escape(c.id)+'" aria-current="'+(selected===c.id?'true':'false')+'"><span class="item-meta">'+(c.owner==='agent'?'agent 发起':'你发起')+' · '+kindName[c.kind]+(unread?'<span class="new-dot" aria-label="新话题"></span>':'')+'</span><strong>'+escape(c.title)+'</strong><span class="item-status">'+status+'</span></button>';
  }).join(''):'<p class="list-empty">'+(state.busy?'agent 正在结合任务进展思考。<br>你也可以先开启一个话题。':'暂时没有待讨论的话题。<br>有想法时，随时发起。')+'</p>';
}
function renderDetail() {
  const topic=state?.cards.find(c=>c.id===selected&&!c.archived);
  $('#no-topic').hidden=Boolean(topic);$('#topic-content').hidden=!topic;
  if(!topic)return;
  $('#topic-title').textContent=topic.title;
  $('#topic-meta').textContent=(topic.owner==='agent'?'agent 发起':'你发起')+' · '+kindName[topic.kind]+' · '+(topic.created?time(topic.created):'先前的讨论');
  const list=$('#messages');
  const atBottom=list.scrollHeight-list.scrollTop-list.clientHeight<90;
  if(list.dataset.topic!==topic.id){list.replaceChildren();list.dataset.topic=topic.id;}
  let opening=$('#opening');
  const openingKey=JSON.stringify({description:topic.description,status:topic.status,answer:topic.answer,options:topic.options,delivery:topic.delivery});
  if(topic.owner==='agent'&&(!opening||opening.dataset.key!==openingKey)) {
    const el=document.createElement('article');el.id='opening';el.className='message';el.dataset.key=openingKey;
    let actions='';
    if(topic.status!=='resolved') {
      const options=topic.kind==='suggestion'?['采纳','不采纳']:(topic.options||[]);
      actions=options.map(v=>'<button '+(topic.kind==='suggestion'?'data-confirm':'data-option')+'="'+escape(v)+'">'+escape(v)+'</button>').join('');
      actions+='<button class="quiet-option" data-confirm="稍后">稍后再聊</button>';
      if(topic.kind!=='suggestion')actions+='<small>选择后可以补充，再发送回复。</small>';
    } else actions='<small>已确认：'+escape(topic.answer)+'</small>';
    el.innerHTML='<div class="message-label"><strong>讨论 agent</strong><span>发起这个话题</span></div><div class="message-body">'+escape(topic.description)+'</div><div class="topic-options">'+actions+'</div>'+(topic.source_text?'<details class="topic-source"><summary>为什么现在聊这个</summary><p>'+escape(topic.source_text)+'</p></details>':'');
    if(opening)opening.replaceWith(el);else list.prepend(el);
  }
  const known=new Set([...list.querySelectorAll('[data-id]')].map(e=>e.dataset.id));
  state.messages.filter(m=>m.topic_id===selected).forEach(m=>{
    if(known.has(m.id))return;
    const el=document.createElement('article');el.className='message '+m.role;el.dataset.id=m.id;
    if(['system','error'].includes(m.role))el.textContent=m.text;
    else el.innerHTML='<div class="message-label"><strong>'+(m.role==='user'?'你':'讨论 agent')+'</strong><time>'+time(m.time)+'</time></div><div class="message-body">'+escape(m.text)+'</div>';
    list.append(el);
  });
  if(atBottom||!known.size)list.scrollTop=list.scrollHeight;
  const running=state.busy&&state.active_job?.topic_id===selected;
  const queued=state.jobs?.some(j=>j.topic_id===selected);
  $('#thinking').hidden=!running&&!queued;
  $('#thinking').textContent=running?'agent 正在思考这个话题…':'已排队，agent 会继续回应。';
  const last=state.outbox.filter(i=>i.topic_id===selected).at(-1);
  $('#delivery').textContent=last?(last.status==='received'?'✓ Codex 已读取你提交的结论':'结论已提交 · 等待 Codex 读取'):'';
}
async function act(payload) {
  if(locked)return null;
  const target=session;locked=true;$('#send').disabled=true;
  const key=prefix+'retry-'+target;
  let previous;try{previous=JSON.parse(localStorage.getItem(key)||'null');}catch{}
  const signature=JSON.stringify(payload);
  const request=previous?.signature===signature?previous.request:{...payload,session:target,request_id:crypto.randomUUID()};
  localStorage.setItem(key,JSON.stringify({signature,request}));
  try {const result=await api('/api/action',request);localStorage.removeItem(key);render(result);return result.receipts[request.request_id]||{};}
  catch(e){toast(e.message);return null;}
  finally{locked=false;$('#send').disabled=false;}
}
function openTopicDialog(){$('#topic-dialog').showModal();$('#new-text').focus();}
$('#new-topic').onclick=openTopicDialog;$('#empty-new').onclick=openTopicDialog;
$('#topic-form').onsubmit=async e=>{
  e.preventDefault();const text=$('#new-text').value.trim();if(!text)return;
  const receipt=await act({type:'start_topic',text,title:$('#new-title').value.trim()});
  if(receipt){$('#topic-dialog').close();$('#topic-form').reset();selectTopic(receipt.topic_id);}
};
$('#composer').onsubmit=async e=>{
  e.preventDefault();const text=$('#input').value.trim();const target=selected;if(!text||!target)return;
  if(await act({type:'chat',topic_id:target,text})){localStorage.removeItem(draftKey(target));if(selected===target)$('#input').value='';}
};
$('#input').oninput=()=>localStorage.setItem(draftKey(),$('#input').value);
$('#input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('#composer').requestSubmit();}};
$('#back').onclick=()=>$('.workspace').classList.remove('detail-open');
$('#forward').onclick=()=>{$('#forward-text').value=$('#input').value;$('#forward-dialog').showModal();};
$('#forward-form').onsubmit=async e=>{
  e.preventDefault();const text=$('#forward-text').value.trim();if(!text)return;
  const topic=state.cards.find(c=>c.id===selected);
  const payload=topic.owner==='agent'&&topic.status!=='resolved'?{type:'card',card_id:topic.id,value:topic.kind==='decision'&&!topic.options.includes(text)?'补充：'+text:text}:{type:'forward',topic_id:selected,text};
  if(await act(payload)){$('#forward-dialog').close();toast('已保存，等待 Codex 读取');}
};
$('#retry').onclick=()=>act({type:'retry'});
document.addEventListener('click',async e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.dataset.topic)selectTopic(b.dataset.topic);
  if(b.dataset.filter){filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(n=>{n.classList.toggle('active',n===b);n.setAttribute('aria-pressed',String(n===b));});renderTopics();}
  if(b.dataset.close)document.getElementById(b.dataset.close).close();
  if(b.dataset.option){$('#input').value=b.dataset.option;localStorage.setItem(draftKey(),b.dataset.option);$('#input').focus();}
  if(b.dataset.confirm)await act({type:'card',card_id:selected,value:b.dataset.confirm});
});
async function sessions(){
  const items=await api('/api/sessions');
  $('#history').innerHTML=items.map((s,i)=>'<option value="'+s.id+'" '+(s.id===session?'selected':'')+'>'+new Date(s.created*1000).toLocaleString('zh-CN')+' · 讨论 '+(items.length-i)+'</option>').join('');
  return items;
}
$('#history').onchange=async e=>{if(locked)return;bind(e.target.value);try{render(await api('/api/state?session='+session));$('#task-details').open=false;}catch(error){toast(error.message);}};
async function poll(){
  try{render(await api('/api/state?session='+encodeURIComponent(session)));}
  catch{$('#connection').textContent='连接中断，重连中';}
  finally{setTimeout(poll,1200);}
}
async function boot(){
  try{const items=await sessions();if(!items.some(s=>s.id===session))session=items[0]?.id||(await api('/api/sessions',{})).id;
    bind(session);render(await api('/api/state?session='+session));await sessions();poll();}
  catch{$('#connection').textContent='连接中断，重连中';setTimeout(boot,3000);}
}
boot();
