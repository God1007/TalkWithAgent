'use strict';
const $ = s => document.querySelector(s);
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let session = new URL(location.href).searchParams.get('session') || localStorage.getItem('sidecar-session');
let state, version = -1, cardSnapshot = '', locked = false, toastTimer;
const drafts = new Map();
const time = value => new Date(value * 1000).toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'});
function toast(text) { $('#toast').textContent = text; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 5000); }
async function api(path, body) {
  const response = await fetch(path, body ? {method:'POST', headers:{'Content-Type':'application/json','X-Sidecar':'1'}, body:JSON.stringify(body), signal:AbortSignal.timeout(10000)} : {signal:AbortSignal.timeout(6000)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '请求没有成功，请重试');
  return result;
}
function bind(id) {
  session = id; version = -1; cardSnapshot = ''; localStorage.setItem('sidecar-session', id);
  const url = new URL(location.href); url.searchParams.set('session', id); history.replaceState(null, '', url);
  $('#input').value = localStorage.getItem('sidecar-draft-' + id) || '';
}
function render(next) {
  if (next.id !== session) return;
  state = next; $('#connection').textContent = '已连接 · 本地保存';
  $('#send').disabled = state.busy || locked; $('#input').placeholder = state.busy ? '可以先写下下一条，回复结束后发送…' : '聊聊当前任务，或继续追问…';
  if (state.version === version) return;
  version = state.version;
  $('#task-name').textContent = state.title;
  $('#task-update').textContent = state.events.at(-1)?.text || '执行端尚未同步进展';
  $('#task-status').textContent = {working:'Codex 执行中',waiting:'Codex 等待决定',completed:'本轮已完成'}[state.status];
  $('#main-id').textContent = state.main_thread || '尚未绑定主任务';
  $('#discussion-id').textContent = state.codex_thread || '发送消息后创建，后续持续复用';
  $('#session-id').textContent = state.id;
  $('#agent-state').textContent = state.busy ? '正在思考' : state.codex_thread ? 'Codex · 会话已连接' : 'Codex · 发送消息开始讨论';
  const list = $('#messages'); const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 100;
  const oldIds = new Set([...list.children].map(n => n.dataset.id));
  if (list.dataset.session !== session) { list.replaceChildren(); list.dataset.session = session; oldIds.clear(); }
  state.messages.forEach(msg => {
    if (oldIds.has(msg.id)) return;
    const div = document.createElement('div'); div.className = 'message ' + msg.role; div.dataset.id = msg.id;
    if (['system','error'].includes(msg.role)) div.textContent = msg.text;
    else div.innerHTML = `<div class="message-label">${msg.role === 'user' ? '' : '<span class="avatar" aria-hidden="true">Ⅲ</span>'}${msg.role === 'user' ? '你' : msg.role === 'welcome' ? 'Sidecar · 欢迎' : '讨论 agent'}<time>${time(msg.time)}</time></div><div class="message-body">${escape(msg.text)}</div>`;
    list.append(div);
  });
  if (nearBottom || !oldIds.size) list.scrollTop = list.scrollHeight;
  $('#thinking').hidden = !state.busy;
  $('#quick-prompts').hidden = state.messages.some(m => m.role === 'user');
  const count = state.cards.filter(c => c.status !== 'resolved').length;
  $('#pending-count').textContent = count; $('#tab-count').textContent = count;
  const snapshot = JSON.stringify(state.cards);
  if (snapshot !== cardSnapshot) {
    const focusedId = document.activeElement?.id, pos = document.activeElement?.selectionStart;
    $('#cards').querySelectorAll('textarea').forEach(e => drafts.set(e.id, e.value));
    $('#cards').innerHTML = state.cards.map(cardHTML).join(''); cardSnapshot = snapshot;
    const focused = focusedId && document.getElementById(focusedId);
    if (focused && focused.tagName === 'TEXTAREA') { focused.focus(); focused.setSelectionRange(pos,pos); }
  }
  $('#outbox').innerHTML = state.outbox.length ? '<h3>回传记录</h3>' + state.outbox.slice(-5).map(i => `<div class="delivery-item">${escape(i.text)}<small>${i.status === 'received' ? '✓ Codex 已读取' : '已保存 · 等待 Codex 读取'} · ${time(i.time)}</small></div>`).join('') : '';
}
function cardHTML(c) {
  const names = {decision:'待决策',suggestion:'建议',question:'问题'};
  const labels = {decision:c.demo?'示例交互':'需要你的决定',suggestion:'不阻塞执行',question:'可以稍后回答'};
  let body = '';
  if (c.status === 'resolved') body = `<p class="result">${escape(c.answer)}<br>${c.demo ? '✓ 示例已完成' : c.delivery === 'received' ? '✓ Codex 已读取' : '已保存 · 等待 Codex 读取'}</p>`;
  else if (c.kind === 'decision') body = `<div class="card-buttons"><button data-card="${c.id}" data-value="项目目录">项目目录</button><button data-card="${c.id}" data-value="用户目录">用户目录</button></div><details><summary>先讨论，再决定</summary><p>项目目录便于共享，用户目录适合个人偏好。你也可以在对话里继续追问。</p></details>`;
  else if (c.kind === 'suggestion') body = `<div class="card-buttons"><button data-card="${c.id}" data-value="采纳">采纳建议</button><button class="secondary" data-card="${c.id}" data-value="不采纳">不采纳</button></div><details><summary>补充说明</summary><form data-card-form="${c.id}" data-prefix="补充："><label class="sr-only" for="card-${c.id}">补充说明</label><textarea id="card-${c.id}" required maxlength="1800" placeholder="说说你的考虑…">${escape(drafts.get('card-'+c.id)||'')}</textarea><button type="submit">提交补充</button></form></details>`;
  else body = `<form data-card-form="${c.id}"><label class="sr-only" for="card-${c.id}">${escape(c.title)}</label><textarea id="card-${c.id}" required maxlength="1800" placeholder="例如：通常同时推进 2–3 个任务">${escape(drafts.get('card-'+c.id)||'')}</textarea><div class="card-buttons"><button type="submit">回复</button>${c.status === 'deferred' ? '<span class="result">已暂存，随时可答</span>' : `<button class="secondary" type="button" data-card="${c.id}" data-value="稍后">稍后回答</button>`}</div></form>`;
  return `<article class="card ${c.kind} ${c.status}"><div class="card-kind"><strong>${names[c.kind]}</strong><span>${labels[c.kind]}</span></div><h3>${escape(c.title)}</h3><p>${escape(c.description)}</p>${body}</article>`;
}
async function act(payload) {
  if (locked) return false;
  locked = true; $('#send').disabled = true;
  const key = 'sidecar-retry-' + session;
  const previous = JSON.parse(localStorage.getItem(key) || 'null');
  const fingerprint = JSON.stringify(payload);
  const request = previous?.fingerprint === fingerprint ? previous.request : {...payload, session, request_id:crypto.randomUUID()};
  localStorage.setItem(key, JSON.stringify({fingerprint,request}));
  try { const result = await api('/api/action', request); localStorage.removeItem(key); render(result); return true; }
  catch (err) { toast(err.name === 'TimeoutError' ? '连接超时。内容已保留，重试不会重复提交。' : err.message); return false; }
  finally { locked = false; $('#send').disabled = Boolean(state?.busy); }
}
$('#composer').addEventListener('submit', async event => {
  event.preventDefault(); const text = $('#input').value.trim(); if (!text || state?.busy) return;
  if (await act({type:'chat',text})) { $('#input').value = ''; localStorage.removeItem('sidecar-draft-'+session); $('#messages').scrollTop = $('#messages').scrollHeight; }
});
$('#input').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {e.preventDefault(); $('#composer').requestSubmit();} });
$('#input').addEventListener('input', () => localStorage.setItem('sidecar-draft-'+session,$('#input').value));
document.addEventListener('click', async e => {
  const button = e.target.closest('button'); if (!button) return;
  if (button.dataset.prompt) { $('#input').value = button.dataset.prompt; $('#input').focus(); }
  if (button.dataset.card) await act({type:'card',card_id:button.dataset.card,value:button.dataset.value});
  if (button.dataset.view) { $('.workspace').dataset.view = button.dataset.view; document.querySelectorAll('[data-view].active').forEach(n=>n.classList.remove('active')); button.classList.add('active'); }
});
$('#cards').addEventListener('submit', async e => {e.preventDefault(); const f=e.target; const text=f.querySelector('textarea').value.trim(); if(text) await act({type:'card',card_id:f.dataset.cardForm,value:(f.dataset.prefix||'')+text});});
$('#demo-card').addEventListener('click', async () => { await act({type:'demo_decision'}); if(innerWidth<=760) document.querySelector('button[data-view=cards]').click(); });
$('#forward').addEventListener('click', () => { $('#forward-text').value = $('#input').value; $('#forward-dialog').showModal(); });
$('#close-dialog').addEventListener('click', () => $('#forward-dialog').close());
$('#forward-form').addEventListener('submit', async e => {e.preventDefault(); const text=$('#forward-text').value.trim(); if(text && await act({type:'forward',text})) {$('#forward-dialog').close();toast('已保存到回传队列，等待 Codex 读取');} });
async function sessions() { const items=await api('/api/sessions'); $('#history').innerHTML=items.map((s,i)=>`<option value="${s.id}" ${s.id===session?'selected':''}>${new Date(s.created*1000).toLocaleString('zh-CN')} · 讨论 ${items.length-i}</option>`).join('');return items; }
$('#new-session').addEventListener('click', async () => {if(locked)return;try {const next=await api('/api/sessions',{});bind(next.id);render(next);await sessions();toast('已新建讨论，旧会话保留在关联信息中');}catch(e){toast(e.message);} });
$('#history').addEventListener('change', async e => {bind(e.target.value);render(await api('/api/state?session='+session));});
async function poll() {
  try { if(session) render(await api('/api/state?session='+encodeURIComponent(session))); }
  catch(e) {$('#connection').textContent='连接中断 · 正在重连';}
  finally { setTimeout(poll,1200); }
}
async function boot() {
  try {
    const items = await sessions();
    if (!items.some(s=>s.id===session)) session=items[0]?.id || (await api('/api/sessions',{})).id;
    bind(session);render(await api('/api/state?session='+session));await sessions();poll();
  } catch(e) {$('#connection').textContent='连接中断 · 正在重连';toast('无法连接本地服务，正在重试');setTimeout(boot,3000);}
}
boot();
