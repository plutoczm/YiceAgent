// Deterministic DOM/HTTP doubles execute the actual workbench event handlers.
// This regression supplements the real browser/HTTP evidence; it is not an E2E browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class El {
  constructor(id = '') {
    this.id = id; this.value = ''; this.textContent = ''; this.children = []; this.handlers = {};
    this.dataset = {}; this.classList = { toggle() {} };
  }
  append(...els) { this.children.push(...els); }
  replaceChildren(...els) { this.children = els; this.textContent = ''; }
  addEventListener(name, handler) { this.handlers[name] = handler; }
}
const text = (el) => el.textContent + el.children.map(text).join('');
const source = fs.readFileSync(path.join(__dirname, '../../services/harness/backend/src/app/demo/static/app.js'), 'utf8');

async function scenario(prefix, outcome, readFails = false) {
  const els = new Map(), calls = [];
  const get = id => { if (!els.has(id)) els.set(id, new El(id)); return els.get(id); };
  const message = { message_id: 'MSG-1', ticket_id: 'TKT-1', sender_role: prefix === 'operator' ? 'agent' : 'customer', body: 'SENT_ONCE', created_at: '2026-10-02T00:00:00Z' };
  const persona = prefix === 'operator' ? 'operator-a' : 'customer-a1';
  let reads = 0;
  const context = {
    document: { getElementById: get, createElement: () => new El(), querySelectorAll: s => s === 'button' ? [...els.values()] : [] },
    crypto: { randomUUID: () => '12345678-abcd' },
    fetch: async (url, options) => {
      const body = options.body ? JSON.parse(options.body) : null; calls.push({ url, body });
      if (url === '/api/demo/session') return { ok: true, json: async () => ({ persona, mode: 'offline' }) };
      const action = body.payload.action;
      let result = null, error = null, status = 'completed';
      if (action === 'reply') {
        if (outcome === 'acknowledged') { result = message; error = 'internal_error'; status = 'failed'; }
        else if (outcome === 'unknown') { status = 'unknown_outcome'; error = 'downstream_timeout'; }
        else if (outcome === 'rejected') { status = 'failed'; error = 'domain_rejected'; }
        else result = message;
      } else {
        reads++;
        if (readFails) { status = 'failed'; error = 'downstream_unavailable'; }
        else result = action === 'get' ? { ticket_id: 'TKT-1', status: 'assigned', assignee_id: 'operator-a' } : [message];
      }
      return { ok: true, json: async () => ({ run_id: String(calls.length).padStart(32, 'a'), workflow_id: 'ticket_action', status, error, domain_refs: { ticket_id: 'TKT-1' }, steps: [], result }) };
    },
  };
  vm.createContext(context); vm.runInContext(source, context);
  get('persona').value = persona; await get('persona').handlers.change();
  get(prefix + '-ticket-id').value = 'TKT-1'; get(prefix + '-reply').value = 'SENT_ONCE';
  await get('send-' + prefix + '-reply').handlers.click();
  const writes = () => calls.filter(c => c.body?.payload?.action === 'reply').length;
  assert.equal(writes(), 1);
  if (outcome === 'acknowledged' || outcome === 'normal') {
    assert.equal(get(prefix + '-reply').value, '');
    assert.match(text(get(prefix + '-reply-status')), /工单回复已发送/);
    assert.match(text(get(prefix + '-reply-status')), /MSG-1/);
    if (outcome === 'acknowledged') assert.match(text(get('global-status')), /业务结果已确认；运行记录失败/);
    if (!readFails) assert.match(text(get(prefix + '-thread')), /SENT_ONCE/);
    assert.ok(reads > 0);
    await get('send-' + prefix + '-reply').handlers.click();
    assert.equal(writes(), 1, 'an empty previously sent reply must not be resubmitted');
  } else {
    assert.equal(get(prefix + '-reply').value, 'SENT_ONCE');
    assert.doesNotMatch(text(get(prefix + '-reply-status')), /工单回复已发送/);
    assert.equal(reads, 0);
    assert.match(text(get('global-status')), outcome === 'unknown' ? /写入结果未知/ : /领域拒绝/);
  }
}
(async () => {
  for (const prefix of ['operator', 'customer']) {
    for (const outcome of ['acknowledged', 'unknown', 'rejected', 'normal']) await scenario(prefix, outcome);
    await scenario(prefix, 'acknowledged', true);
  }
  console.log('10 workbench interaction regression scenarios passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
