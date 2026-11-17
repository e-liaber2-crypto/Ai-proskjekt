// Bruk den faktiske nettlesarkoden med ein lokal, kontrollert transport.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const code = source.slice(source.indexOf('async function api('), source.indexOf('function storageDescription('));
const config = { ffmpeg_available: true, limits: { max_files: 12 } };
const response = (body, status = 200, type = 'application/json') => ({
  ok: status >= 200 && status < 300, status,
  headers: { get: () => type }, json: async () => body,
});
function runtime(fetch, extra = {}) {
  const context = { fetch, state: { config: null, configPromise: null },
    updateEnvironment() {}, AbortController, FormData, TypeError, SyntaxError,
    setTimeout, clearTimeout, ...extra };
  vm.createContext(context);
  vm.runInContext(code, context);
  return context;
}

test('a failed initial configuration can be retried without reloading the page', async () => {
  let calls = 0;
  const app = runtime(async () => {
    if (++calls === 1) throw new TypeError('offline');
    return response(config);
  });
  await assert.rejects(app.loadConfig(), /kontakt med serveren/);
  assert.equal(app.state.config, null);
  await app.loadConfig();
  assert.equal(app.state.config, config);
  await app.loadConfig();
  assert.equal(calls, 2);
});

test('concurrent navigation shares one configuration request', async () => {
  let calls = 0;
  const app = runtime(async () => { calls++; return response(config); });
  await Promise.all([app.loadConfig(), app.loadConfig(), app.loadConfig()]);
  assert.equal(calls, 1);
});

test('a Render loading page is not accepted as project data', async () => {
  const app = runtime(async () => response('<html>Starting</html>', 200, 'text/html'));
  await assert.rejects(app.loadConfig(), /Render.*vakne/);
  assert.equal(app.state.config, null);
});

test('expired authentication gives a useful error', async () => {
  const app = runtime(async () => response('', 401, 'text/html'));
  await assert.rejects(app.api('/projects'), /logge inn igjen/);
});

test('a timed-out mutation is never submitted twice automatically', async () => {
  let calls = 0, timeout;
  const app = runtime((_url, options) => {
    calls++;
    return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => {
      const error = new Error('aborted'); error.name = 'AbortError'; reject(error);
    }));
  }, { setTimeout(callback) { timeout = callback; return 1; }, clearTimeout() {} });
  const request = app.api('/projects', { method: 'POST', body: JSON.stringify({ name: 'Demo' }) });
  timeout();
  await assert.rejects(request, /oppdater prosjektet/);
  assert.equal(calls, 1);
});

test('invalid configuration does not permanently disable the editor', async () => {
  let calls = 0;
  const app = runtime(async () => response(++calls === 1 ? {} : config));
  await assert.rejects(app.loadConfig(), /Serveroppsettet/);
  await app.loadConfig();
  assert.equal(app.state.config, config);
});
