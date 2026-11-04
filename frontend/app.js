const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const check = async r => {
  if (!r.ok) {
    let d = ''; try { d = (await r.json()).detail; } catch (e) { d = ''; }
    throw new Error(`HTTP ${r.status}${d ? ': ' + String(d).slice(0, 200) : ''}`);
  }
  return r.json();
};
const get = p => fetch('/api' + p).then(check);
const post = (p, b) => fetch('/api' + p, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(b || {})}).then(check);
// Slow AI calls run as background jobs on the server; poll until done (no browser or tunnel timeout can cut them off).
async function runJob(path, body, tick) {
  const r = await post(path, body);
  if (r.state === 'done') return r.result;
  const t0 = Date.now();
  while (Date.now() - t0 < 600000) {
    await new Promise(res => setTimeout(res, 2500));
    if (tick) tick(Math.round((Date.now() - t0) / 1000));
    const j = await get('/jobs/' + r.job);
    if (j.state === 'done') return j.result;
    if (j.state === 'error') throw new Error(j.error);
  }
  throw new Error('timed out after 10 minutes');
}
const money = n => n == null ? '—' : '$' + n.toLocaleString();
const pill = v => `<span class="pill ${esc(String(v).split(' ')[0])}">${esc(v)}</span>`;
const table = (cols, rows, click) => `<table><tr>${cols.map(c => `<th>${c[0]}</th>`).join('')}</tr>${rows.map(r => `<tr ${click ? `class="click" onclick="${click(r)}"` : ''}>${cols.map(c => `<td>${c[1](r)}</td>`).join('')}</tr>`).join('')}</table>`;
const metric = (ico, cls, label, num, foot) => `<div class="card metric"><div class="metrichead"><span class="metricicon ${cls}">${ico}</span>${label}</div><div class="metricnum">${num}</div><div class="metricfoot">${foot}</div></div>`;
let D = {}, openId = null;

document.querySelectorAll('.navitem').forEach(b => b.onclick = () => go(b.dataset.page));
$('searchInput').oninput = () => { go('Orders'); renderOrders(); };
function go(page) {
  document.querySelectorAll('.navitem').forEach(b => b.classList.toggle('active', b.dataset.page === page));
  document.querySelectorAll('main > section').forEach(s => s.hidden = s.id !== 'page-' + page);
}

async function load() {
  const names = ['health','summary','alerts','messages','orders','inventory','suppliers','audit'];
  const res = await Promise.all(names.map(n => get('/' + n)));
  names.forEach((n, i) => D[n] = res[i]);
  render();
}

function renderOrders() {
  const q = $('searchInput').value.toLowerCase();
  const rows = D.orders.filter(o => !q || [o.po, o.supplier, o.part].join(' ').toLowerCase().includes(q));
  if (!rows.length) {
    $('orders').innerHTML = `<div class="pad muted" style="padding-top:16px">${D.orders.length ? 'No order matches the search box at the top. Clear it to see all ' + D.orders.length + ' orders.' : 'No orders are loaded. Click Reset demo; if this stays empty, check the server window for an error.'}</div>`;
    return;
  }
  $('orders').innerHTML = table([['PO',o=>`<b>${esc(o.po)}</b>`],['Supplier',o=>esc(o.supplier)],['Part',o=>esc(o.part)],['Qty',o=>o.qty],['Promised',o=>o.promised_date],['Revised',o=>o.revised_date||'—'],['Status',o=>pill(o.status)],['Note',o=>esc(o.note||'')]], rows);
}

function render() {
  const s = D.summary, name = id => (D.suppliers.find(x => x.supplier_id === id) || {}).name || '—';
  $('sys').textContent = 'AI: ' + D.health.llm; $('datepill').textContent = 'Demo date: ' + D.health.today;
  $('bell').hidden = !s.open_alerts;
  $('metrics').innerHTML = metric('♧','red','Open alerts',s.open_alerts,s.critical+' critical') + metric('⬡','amber','Delayed orders',s.delayed_orders,'from supplier messages') +
    metric('▤','red','Items at risk',s.at_risk_items,'low stock or short cover') + metric('✉','','Messages received',s.messages,'WhatsApp, SMS, email');
  $('alerts').innerHTML = D.alerts.length ? table([['Severity',a=>pill(a.severity)],['Status',a=>pill(a.status)],['PO',a=>`<b>${esc(a.po||'—')}</b>`],['Supplier',a=>esc(name(a.supplier_id))],['Reason',a=>esc(a.reason||a.kind)],['Confidence',a=>a.confidence]], D.alerts, a => `show(${a.id})`)
    : '<div class="pad muted">No alerts yet. Send a message from the panel on the right.</div>';
  $('feed').innerHTML = D.messages.slice(0,6).map(m => `<div class="msg"><small>${esc(m.channel)} · ${esc(m.supplier||m.sender)}</small>${esc(m.text)}</div>`).join('') || '<span class="muted">No messages yet.</span>';
  renderOrders();
  $('sup').innerHTML = table([['Supplier',x=>`<b>${esc(x.name)}</b>`],['Region',x=>esc(x.region)],['Score',x=>x.score],['Rating',x=>pill(x.label)],['Delayed orders',x=>x.delayed_orders],['Single-source parts',x=>x.single_source_parts]], D.suppliers);
  $('inv').innerHTML = table([['Part',i=>`<b>${esc(i.name)}</b>`],['Available',i=>i.available],['Reorder point',i=>i.reorder_point],['Daily use',i=>i.daily_use],['Cover (days)',i=>i.cover_days],['Open PO',i=>esc(i.po||'—')],['Risk',i=>pill(i.risk)]], D.inventory);
  $('audit').innerHTML = table([['Time',a=>esc(a.ts)],['Who',a=>esc(a.actor)],['Action',a=>esc(a.action)],['Detail',a=>esc(a.detail)]], D.audit);
  const counts = {}; D.orders.forEach(o => counts[o.status] = (counts[o.status]||0) + 1);
  $('bars').innerHTML = Object.entries(counts).map(([k,v]) => `<div>${pill(k)} ${v}</div><div class="bar"><span style="width:${v/D.orders.length*100}%"></span></div>`).join('');
  { // rebuild the sender list on every refresh so it can never go stale after a Reset or a CSV change
    const sel = $('sender'), keep = sel.value;
    sel.innerHTML = D.suppliers.map(x => `<option value="${esc(x.phone)}">${esc(x.name)}</option>`).join('') + '<option value="+911234567890">Unknown sender</option>';
    if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
  }
  renderPlans();
  if (openId) show(openId, true);
}

async function show(id, quiet) {
  openId = id;
  const a = await get('/alerts/' + id), e = a.exposure, decided = !['pending_approval','needs_review'].includes(a.status);
  $('detail').innerHTML = `<p>${pill(a.severity)} ${pill(a.status)} <span class="muted">confidence ${a.confidence}</span></p><div class="explain">${esc(a.explanation)}</div>` +
    (e ? `<p class="muted">Stock-out ${e.stockout_date} · new arrival ${e.arrival_date} · gap ${e.gap_days} days · revenue at risk ${money(e.revenue_at_risk)}</p>` : '') +
    (a.scenarios ? `<div class="tablewrap">${table([['Option',s=>(s.key===a.recommended?'★ ':'')+esc(s.label)],['Stop days',s=>s.stop_days ?? '—'],['Extra cost',s=>money(s.extra_cost)],['Revenue loss',s=>money(s.revenue_loss)],['Total',s=>`<b>${money(s.total)}</b>`],['Note',s=>esc(s.note)],['',s=>s.feasible && !decided ? `<button class="btn p" onclick="decide(${a.id},'${s.key}',true)">Approve</button>` : '']], a.scenarios)}</div>` : '') +
    (decided ? `<p class="muted">Decision: ${esc(a.status)} ${esc(a.decided_option||'')} by ${esc(a.decided_by||'')}</p>` : `<div class="row"><button class="btn" onclick="decide(${a.id},'none',false)">Reject / dismiss</button><button class="btn p" onclick="go('AI Assistant')">AI response plan →</button></div>`);
  if (!quiet) $('detail').scrollIntoView({behavior:'smooth', block:'nearest'});
}
const EXAMPLES = [
  {ch:'whatsapp', who:'All India Cargo', t:'AIC-HYP-20261051 will be 8 days late, vehicle breakdown'},
  {ch:'sms', who:'DHL', t:'DHL-HYP-20261053 delayed 5 days, mishandling at the hub'},
  {ch:'whatsapp', who:'All India Cargo', t:'AIC-HYP-20261001 will be 6 days late, damaged carton'},
  {ch:'email', who:'DHL', t:'DHL-HYP-20261054 will be 1 day late'}
];
function ex(i) {
  const e = EXAMPLES[i];
  $('ch').value = e.ch;
  const o = [...$('sender').options].find(o => o.text.includes(e.who));
  if (o) $('sender').value = o.value;
  $('text').value = e.t; $('text').focus();
}
async function send() {
  const t = $('text').value.trim();
  if (!t) { $('sendstate').textContent = 'Type or pick a message first.'; return; }
  $('sendstate').textContent = 'Sending…';
  let r;
  try { r = await post('/ingest', {channel:$('ch').value, sender:$('sender').value, text:t}); }
  catch (e) { $('sendstate').textContent = 'Could not send: ' + e.message; return; }
  $('text').value = '';
  $('sendstate').textContent = r.alert ? 'Sent. Alert #' + r.alert.id + ' created.' : 'Sent. Logged, no action needed.';
  if (r.alert) openId = r.alert.id;
  await load();
  if (r.alert) show(r.alert.id);
}
const waiting = a => ['pending_approval','needs_review'].includes(a.status);
function planHtml(p) {
  const flags = (p.flags || []).length ? `<div class="warn">Check these figures before relying on the text: ${p.flags.map(esc).join(', ')} (they are not in the verified data).</div>` : '';
  return `<div class="explain">${esc(p.summary)}</div>${flags}` +
    `<div class="tablewrap">${table([['Step',a=>esc(a.step)],['Owner',a=>esc(a.owner)],['When',a=>esc(a.when)]], p.actions)}</div>` +
    `<p class="muted" style="margin:14px 0 4px">Draft message to the partner</p><div class="msg">${esc(p.supplier_message)}</div>` +
    `<button class="btn" data-copy="${esc(p.supplier_message)}" onclick="copyText(this)">Copy message</button>` +
    `<p class="muted" style="margin:14px 0 4px">Questions to ask</p><ul>${(p.questions || []).map(q => `<li>${esc(q)}</li>`).join('')}</ul>` +
    `<p class="muted">Source: ${esc(p.source)} · model: ${esc(p.model)} · ${esc(p.generated_at || '')}</p>`;
}
function renderPlans() {
  const open = D.alerts.filter(waiting);
  $('plans').innerHTML = open.length ? open.map(a => `<div class="planbox"><div class="row" style="justify-content:space-between;align-items:center">
      <div>${pill(a.severity)} ${pill(a.status)} <b>${esc(a.po || a.kind)}</b> <span class="muted">${esc(a.reason || '')}</span></div>
      <button class="btn p" id="plan-btn-${a.id}" onclick="genPlan(${a.id}, ${a.plan ? 'true' : 'false'})">${a.plan ? 'Regenerate plan' : 'Generate AI plan'}</button></div>
      <div id="plan-${a.id}">${a.plan ? planHtml(a.plan) : '<span class="muted">No plan yet. Click Generate.</span>'}</div></div>`).join('')
    : '<span class="muted">No alerts are waiting for a decision. Send a partner message from the Dashboard.</span>';
}
async function genPlan(id, refresh) {
  const b = $('plan-btn-' + id); b.disabled = true; b.textContent = 'Thinking…';
  try { await runJob(`/alerts/${id}/plan`, {refresh}, sec => b.textContent = `Thinking… ${sec}s`); await load(); }
  catch (e) {
    b.disabled = false; b.textContent = 'Try again';
    $('plan-' + id).innerHTML = `<div class="warn">Could not generate a plan: ${esc(e.message)}. Check the server window for details.</div>`;
  }
}
function copyText(btn) { navigator.clipboard.writeText(btn.dataset.copy); btn.textContent = 'Copied'; setTimeout(() => btn.textContent = 'Copy message', 1500); }
async function decide(id, opt, ok) { await post(`/alerts/${id}/decision`, {option:opt, approve:ok}); await load(); }
async function ask(q) {
  if (typeof q === 'string') $('q').value = q;
  $('ans').textContent = 'Thinking…';
  try {
    const r = await runJob('/ask', {question: $('q').value}, sec => $('ans').textContent = `Thinking… ${sec}s (a local model can take a minute)`);
    const flags = (r.flags || []).length ? `<div class="warn">Check these figures: ${r.flags.map(esc).join(', ')} (not in the verified data).</div>` : '';
    $('ans').innerHTML = `<div style="white-space:pre-wrap">${esc(r.answer)}</div>${flags}<p class="muted" style="margin:10px 0 0">Source: ${esc(r.source)} · model: ${esc(r.model)} · answered from your live orders, stock and alerts</p>`;
  } catch (e) { $('ans').innerHTML = `<div class="warn">Could not get an answer: ${esc(e.message)}. Check the server window for details.</div>`; }
}
async function resetDemo() { try { await post('/reset'); } catch (e) { alert('Reset failed: ' + e.message + '. Check the server window for the error.'); return; } openId = null; $('detail').innerHTML = '<span class="muted">Select an alert above.</span>'; await load(); }
load();
