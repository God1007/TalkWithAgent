'use strict';
const $ = s => document.querySelector(s);
const escape = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const prefix = 'talkwithagent-';
// Only this tab's URL determines its task. Never pick another task from storage.
const session = new URL(location.href).searchParams.get('session');
let activeAgent = new URL(location.href).searchParams.get('agent') || localStorage.getItem(prefix+'agent-'+session) || session;
let state, replyTo = null, locked = false, version = -1, toastTimer;
const time = v => new Date(v * 1000).toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'});
const kindName = {question:'问题',suggestion:'建议',decision:'需要决定'};
const draftKey = () => prefix+'draft-'+session+(activeAgent===session?'':'-'+activeAgent);
function saveDraft() { localStorage.setItem(draftKey(),JSON.stringify({text:$('#input').value,replyTo})); }
function loadDraft() {
  let draft;try{draft=JSON.parse(localStorage.getItem(draftKey())||'{}');}catch{}
  $('#input').value=draft?.text||'';replyTo=draft?.replyTo||null;
}
async function selectAgent(id, next) {
  saveDraft();activeAgent=id;version=-1;state=null;loadDraft();
  localStorage.setItem(prefix+'agent-'+session,id);
  const url=new URL(location.href);url.searchParams.set('agent',id);history.replaceState(null,'',url);
  $('#messages').querySelectorAll('[data-id]').forEach(n=>n.remove());
  $('#empty').hidden=false;$('#send').disabled=true;
  try{render(next||await api('/api/state?session='+encodeURIComponent(session)+'&agent='+encodeURIComponent(id)));}
  catch(e){toast(e.message);}
}
function toast(text) {
  $('#toast').textContent=text;$('#toast').hidden=false;
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,4500);
}
async function api(path, body) {
  const response=await fetch(path,{...(body?{method:'POST',headers:{'Content-Type':'application/json','X-TalkWithAgent':'1'},body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(10000)});
  const result=await response.json();
  if(!response.ok)throw new Error(result.error||'请求失败，请重试');
  return result;
}
function setReply(id) {
  replyTo=id;
  const card=state?.cards.find(c=>c.id===id&&!c.archived&&c.status!=='resolved');
  if(!card)replyTo=null;
  $('#reply-context').hidden=!replyTo;
  $('#reply-label').textContent=card?'回复：'+card.title:'';
  $('#input').maxLength=replyTo?2000:6000;
}
function cardHTML(card) {
  let actions='';
  if(card.status!=='resolved') {
    const options=card.kind==='suggestion'?['采纳','不采纳']:(card.options||[]);
    actions=options.map(v=>'<button data-card="'+escape(card.id)+'" data-option="'+escape(v)+'">'+escape(v)+'</button>').join('');
    actions+='<button data-card="'+escape(card.id)+'" data-reply="true">补充回复</button><button class="quiet-option" data-card="'+escape(card.id)+'" data-later="true">稍后再聊</button>';
  } else actions='<small>已回答：'+escape(card.answer)+' · '+(card.delivery==='received'?'Codex 已读取':'等待 Codex 读取')+'</small>';
  return '<div class="message-label"><strong>'+escape(state.agent_name||'讨论 agent')+'</strong><span>'+kindName[card.kind]+'</span><time>'+time(card.created)+'</time></div><h2 class="card-title">'+escape(card.title)+'</h2><div class="message-body">'+escape(card.description)+'</div><div class="card-options">'+actions+'</div>';
}
function render(next) {
  if(next.id!==activeAgent||next.version<version)return;
  $('#connection').textContent='已连接';
  const options=(next.agents||[]).map(a=>'<option value="'+escape(a.id)+'" '+(a.id===activeAgent?'selected':'')+'>'+escape(a.name)+(a.busy?' · 思考中':a.queued?' · 等待回复':'')+'</option>').join('');
  if($('#agents').dataset.options!==options){$('#agents').innerHTML=options;$('#agents').dataset.options=options;}
  if(version===next.version)return;
  state=next;version=state.version;
  $('.brand').href='/?session='+encodeURIComponent(session);
  $('#task-name').textContent=state.title;
  $('#task-status').textContent={working:'执行中',waiting:'等待决定',completed:'已完成'}[state.status];
  const mainMessages=state.main_context?.messages||[];
  $('#task-update').textContent=mainMessages.filter(m=>m.role==='assistant').at(-1)?.text||state.events.filter(e=>e.kind==='progress'&&!e.archived).at(-1)?.text||'等待任务进展';
  $('#main-id').textContent=state.main_thread||'未绑定';
  $('#discussion-id').textContent=state.codex_thread||'首次回复时建立';
  $('#workspace-path').textContent=state.workspace_dir||state.main_context?.cwd||'等待主会话同步';
  $('#agent-focus').textContent=state.agent_focus||'共享当前 Codex 会话';
  $('#agent-focus').title=state.agent_focus||'';
  $('#sync-state').textContent=state.context_error?'同步中断':state.context_synced_at?'会话已同步 · '+time(state.context_synced_at):'正在同步会话';
  $('#sync-detail').textContent=state.context_error||(state.context_synced_at?'已同步 '+mainMessages.length+' 条用户消息与 agent 可见回复。'+(state.main_context.truncated?'较早内容已截断。':''):'正在读取绑定的 Codex 会话。');
  $('#agent-state').textContent=state.busy?'agent 正在思考…':state.jobs?.length?'消息已保存，等待回复':state.auto_discuss===false?'随时可以继续讨论':'agent 正在关注当前任务';
  const error=state.context_error||state.agent_error||(state.codex_available===false?'没有找到 Codex CLI，请安装并登录后重启服务。':'');
  $('#agent-error').hidden=!error;$('#agent-error').textContent=error;
  $('#retry').hidden=!state.agent_error;
  $('#send').disabled=locked||!state.main_thread;
  const cards=state.cards.filter(c=>!c.archived);
  const ids=new Set(cards.map(c=>c.id));
  const entries=[...cards.filter(c=>c.owner==='agent').map(c=>({id:c.id,time:c.created,role:'assistant',html:cardHTML(c)})),
    ...state.messages.filter(m=>!m.archived&&(!m.topic_id||ids.has(m.topic_id))).map(m=>({...m,
      html:['system','error'].includes(m.role)?escape(m.text):'<div class="message-label"><strong>'+(m.role==='user'?'你':escape(state.agent_name||'讨论 agent'))+'</strong><time>'+time(m.time)+'</time></div><div class="message-body">'+escape(m.text)+'</div>'}))].sort((a,b)=>a.time-b.time);
  const list=$('#messages'),atBottom=list.scrollHeight-list.scrollTop-list.clientHeight<90;
  const existing=new Map([...list.querySelectorAll('[data-id]')].map(n=>[n.dataset.id,n]));
  const active=new Set(entries.map(e=>e.id));
  existing.forEach((node,id)=>{if(!active.has(id))node.remove();});
  $('#empty').hidden=entries.length>0;
  for(const entry of entries) {
    let node=existing.get(entry.id);
    if(!node){node=document.createElement('article');node.dataset.id=entry.id;list.append(node);}
    node.className='message '+entry.role;
    if(node.dataset.content!==entry.html){node.innerHTML=entry.html;node.dataset.content=entry.html;}
  }
  if(atBottom)list.scrollTop=list.scrollHeight;
  setReply(replyTo);
  const last=state.outbox.filter(m=>!m.archived&&(!m.topic_id||ids.has(m.topic_id))).at(-1);
  $('#delivery').textContent=last?(last.status==='received'?'✓ Codex 已读取你提交的内容':'已提交 · 等待 Codex 读取'):'';
}
async function act(payload, path='/api/action') {
  if(locked||!session)return false;
  locked=true;$('#send').disabled=true;
  $('#agents').disabled=true;$('#create-agent').disabled=true;$('#add-agent').disabled=true;
  const key=prefix+'retry-'+activeAgent+'-'+path;
  let previous;try{previous=JSON.parse(localStorage.getItem(key)||'null');}catch{}
  const signature=JSON.stringify(payload);
  const request=previous?.signature===signature?previous.request:{...payload,session:activeAgent,request_id:crypto.randomUUID()};
  localStorage.setItem(key,JSON.stringify({signature,request}));
  try{const result=await api(path,request);localStorage.removeItem(key);
    if(path==='/api/agents')await selectAgent(result.id,result);else render(result);
    return true;}
  catch(e){toast(e.message);return false;}
  finally{locked=false;$('#send').disabled=!state?.main_thread;$('#agents').disabled=false;$('#create-agent').disabled=false;$('#add-agent').disabled=false;}
}
$('#agents').onchange=e=>{if(!locked)selectAgent(e.target.value);};
$('#add-agent').onclick=()=>{
  if(!state)return;
  let count=2;const names=new Set(state.agents.map(a=>a.name));while(names.has('讨论 agent '+count))count++;
  $('#agent-name').value='讨论 agent '+count;$('#agent-instructions').value='';
  $('#agent-dialog').showModal();$('#agent-name').focus();
};
$('#agent-form').onsubmit=async e=>{
  e.preventDefault();
  if(await act({name:$('#agent-name').value.trim(),focus:$('#agent-instructions').value.trim()},'/api/agents'))$('#agent-dialog').close();
};
$('#composer').onsubmit=async e=>{
  e.preventDefault();const text=$('#input').value.trim();if(!text)return;
  const card=state?.cards.find(c=>c.id===replyTo);
  const payload=card?{type:'card',card_id:card.id,value:card.kind==='decision'&&!card.options.includes(text)?'补充：'+text:text}:{type:'chat',text};
  if(await act(payload)){
    // Preserve anything typed while the request was in flight.
    if($('#input').value.trim()===text){$('#input').value='';setReply(null);saveDraft();}
  }
};
$('#input').oninput=saveDraft;
$('#input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('#composer').requestSubmit();}};
$('#cancel-reply').onclick=()=>{setReply(null);saveDraft();};
$('#forward').onclick=()=>{if(!session)return;$('#forward-text').value=$('#input').value;$('#forward-dialog').showModal();};
$('#forward-form').onsubmit=async e=>{
  e.preventDefault();const text=$('#forward-text').value.trim();if(!text)return;
  if(await act({type:'forward',text})){$('#forward-dialog').close();toast('已保存，等待 Codex 读取');}
};
$('#retry').onclick=()=>act({type:'retry'});
document.addEventListener('click',async e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.dataset.close)document.getElementById(b.dataset.close).close();
  if(b.dataset.later)await act({type:'card',card_id:b.dataset.card,value:'稍后'});
  if(b.dataset.reply||b.dataset.option){
    setReply(b.dataset.card);
    if(b.dataset.option)$('#input').value=b.dataset.option;
    saveDraft();$('#input').focus();
  }
});
async function poll(){
  try{render(await api('/api/state?session='+encodeURIComponent(session)+'&agent='+encodeURIComponent(activeAgent)));}
  catch(e){$('#connection').textContent='连接中断';$('#agent-error').hidden=false;$('#agent-error').textContent=e.message;}
  finally{setTimeout(poll,1200);}
}
if(session){
  loadDraft();
  poll();
} else {
  $('#task-name').textContent='尚未关联 Codex 会话';$('#connection').textContent='未关联';$('#sync-state').textContent='';
  $('#agent-state').textContent='在要讨论的 Codex 任务中使用 $talkwithagent，然后打开返回的链接。';
  $('#input').disabled=true;$('#send').disabled=true;$('#forward').disabled=true;$('#agents').disabled=true;$('#add-agent').disabled=true;
}
