// Resultatvisinga må skilje ein eldre eksport frå den siste jobben.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function node(tag, className = '', text = '') {
  return { tag, className, textContent: text, children: [], dataset: {}, innerHTML: '',
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
    setAttribute() {} };
}
const slot = node('div');
const old = { id: 'old', status: 'completed', created_at: '2026-11-04T11:57:22Z',
  output_duration: 15, output_width: 1280, output_height: 720, output_bytes: 1000, plan: [] };
const context = { state: { completed: old, latest: { id: 'new', status: 'failed' }, project: { clips: [] } },
  byId: () => slot, element: node, document: { createElement: node }, icons: () => '',
  duration: String, bytes: String, date: () => '4. november', jobMode: () => 'gemini',
  modeName: () => 'Gemini', scheduleForm: () => {} };
const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const fn = source.slice(source.indexOf('function updateResult()'), source.indexOf('function updateHistory()'));
vm.createContext(context);
vm.runInContext(fn, context);

context.updateResult();
assert.match(slot.children[0].textContent, /Siste forsøk feila.*eldre eksport/);
assert.ok(slot.children.some(item => item.tag === 'video'));
context.state.latest.status = 'processing';
context.updateResult();
assert.match(slot.children[0].textContent, /ny video er under behandling.*eldre eksport/);
context.state.latest = old;
context.updateResult();
assert.ok(!slot.children.some(item => item.className === 'result-status-notice'));
context.state.completed = { ...old, id: 'with-cuts', plan: [{
  clip_id: 'blue', start_seconds: 0.5, end_seconds: 1.5, caption: 'Blå avslutning',
}] };
context.state.latest = context.state.completed;
context.state.project.clips = [{ id: 'blue', original_name: 'bla.mp4' }];
context.updateResult();
const cutSummary = slot.children.find(item => item.className === 'cut-summary');
assert.match(cutSummary.children[0].textContent, /1 utdrag/);
assert.deepEqual(cutSummary.children[1].children[0].children.map(item => item.textContent),
  ['bla.mp4', '0.50–1.50 s i kjelda', 'Blå avslutning']);
context.state.completed = null;
context.state.latest = { id: 'failed-only', status: 'failed' };
context.updateResult();
assert.match(slot.innerHTML, /Ingen ferdig video frå siste forsøk/);
console.log('Result UI: 5 scenarios passed.');
