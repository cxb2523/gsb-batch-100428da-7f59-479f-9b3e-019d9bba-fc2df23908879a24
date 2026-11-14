import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { BlockLog } from '../src/blockLog.js';
import { createProxy } from '../src/server.js';
import { loadConfig } from '../src/config.js';

const silent = { error() {}, warn() {}, info() {}, debug() {} };

// --- unit tests for BlockLog --------------------------------------------------

test('BlockLog: records events and returns them via snapshot()', () => {
  const log = new BlockLog({ maxEvents: 10 });
  log.record({ ts: 1, url: 'http://a.test/x' });
  log.record({ ts: 2, url: 'http://b.test/y' });
  const snap = log.snapshot();
  assert.equal(snap.length, 2);
  assert.equal(snap[0].url, 'http://a.test/x');
  log.destroy();
});

test('BlockLog: ring buffer evicts oldest when maxEvents exceeded', () => {
  const log = new BlockLog({ maxEvents: 3 });
  for (let i = 0; i < 5; i++) log.record({ ts: i, url: `http://x.test/${i}` });
  const snap = log.snapshot();
  assert.equal(snap.length, 3);
  assert.equal(snap[0].ts, 2); // oldest remaining is index 2
  log.destroy();
});

test('BlockLog: snapshot() returns a copy, not the internal array', () => {
  const log = new BlockLog();
  log.record({ ts: 1 });
  const snap = log.snapshot();
  snap.push({ ts: 99 });
  assert.equal(log.snapshot().length, 1);
  log.destroy();
});

test('BlockLog: SSE subscriber receives pushed events in real time', async () => {
  const log = new BlockLog({ pingIntervalMs: 60_000 });

  // Simulate an SSE response with a write buffer.
  const chunks = [];
  const fakeRes = {
    writeHead() {},
    flushHeaders() {},
    write(chunk) { chunks.push(chunk); },
    on(ev, fn) {
      if (ev === 'close') this._onClose = fn;
    },
    destroy() { this._onClose?.(); },
  };

  log.subscribe(fakeRes);

  // Existing events are replayed on subscribe (none yet).
  assert.equal(chunks.length, 0);

  log.record({ ts: 1, url: 'http://x.test/a' });
  assert.equal(chunks.length, 1);
  assert.match(chunks[0], /data:.*x\.test/);

  log.destroy();
});

test('BlockLog: new subscriber receives buffered historical events immediately', () => {
  const log = new BlockLog({ pingIntervalMs: 60_000 });
  log.record({ ts: 1, url: 'http://x.test/a' });
  log.record({ ts: 2, url: 'http://x.test/b' });

  const chunks = [];
  const fakeRes = {
    writeHead() {},
    flushHeaders() {},
    write(chunk) { chunks.push(chunk); },
    on() {},
    destroy() {},
  };
  log.subscribe(fakeRes);

  assert.equal(chunks.length, 2);
  log.destroy();
});

// --- integration: /_proxy/ routes via the running proxy ----------------------

async function listen(server) {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return server.address().port;
}

function directGet(proxyPort, path) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port: proxyPort, path });
    req.on('response', (res) => {
      let body = '';
      res.on('data', (d) => (body += d));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body }));
    });
    req.on('error', reject);
    req.end();
  });
}

test('/_proxy/ serves the HTML dashboard', async (t) => {
  const config = loadConfig({ HTTPS_MODE: 'host-only' });
  const { server } = await createProxy({ config, logger: silent });
  const port = await listen(server);
  t.after(() => server.close());

  const r = await directGet(port, '/_proxy/');
  assert.equal(r.status, 200);
  assert.match(r.headers['content-type'], /text\/html/);
  assert.match(r.body, /block log/);
  assert.match(r.body, /EventSource/);
});

test('/_proxy/log.json returns JSON array (empty initially)', async (t) => {
  const config = loadConfig({ HTTPS_MODE: 'host-only' });
  const { server } = await createProxy({ config, logger: silent });
  const port = await listen(server);
  t.after(() => server.close());

  const r = await directGet(port, '/_proxy/log.json');
  assert.equal(r.status, 200);
  assert.match(r.headers['content-type'], /application\/json/);
  const data = JSON.parse(r.body);
  assert.ok(Array.isArray(data));
});

test('blocked requests appear in /_proxy/log.json', async (t) => {
  const blockFetch = async () => ({ ok: true, status: 200, text: async () => 'User-agent: *\nDisallow: /\n' });
  const config = loadConfig({ HTTPS_MODE: 'host-only' });
  const { server } = await createProxy({ config, fetchImpl: blockFetch, logger: silent });
  const port = await listen(server);
  t.after(() => server.close());

  // Trigger a blocked request via proxy.
  await new Promise((resolve, reject) => {
    const req = http.request({
      host: '127.0.0.1', port,
      method: 'GET', path: 'http://blocked.test/secret',
      headers: { host: 'blocked.test' },
    });
    req.on('response', (res) => { res.resume(); res.on('end', resolve); });
    req.on('error', reject);
    req.end();
  });

  const r = await directGet(port, '/_proxy/log.json');
  const events = JSON.parse(r.body);
  assert.equal(events.length, 1);
  assert.equal(events[0].url, 'http://blocked.test/secret');
  assert.equal(events[0].method, 'GET');
  assert.ok(typeof events[0].ts === 'number');
});

// Failure categories: 5xx, timeout/abort and network failure each get their own label,
// and every one of them lands in the block log with that label attached.
test('robots fetch failures: 5xx, timeout and network error get distinct failureCategory in the log', async (t) => {
  const fetchImpl = async (url) => {
    if (url === 'http://fivexx.test/robots.txt') {
      return { ok: false, status: 503, headers: { get: () => null }, text: async () => '' };
    }
    if (url === 'http://timeout.test/robots.txt') {
      const err = new Error('The operation was aborted');
      err.name = 'AbortError';
      throw err;
    }
    if (url === 'http://neterr.test/robots.txt') {
      throw new Error('ECONNREFUSED');
    }
    throw new Error('unexpected url: ' + url);
  };
  const config = loadConfig({ HTTPS_MODE: 'host-only' });
  const { server } = await createProxy({ config, fetchImpl, logger: silent });
  const port = await listen(server);
  t.after(() => server.close());

  const targets = [
    ['http://fivexx.test/page', 'http-5xx'],
    ['http://timeout.test/page', 'timeout'],
    ['http://neterr.test/page', 'network-error'],
  ];
  for (const [target] of targets) {
    await new Promise((resolve, reject) => {
      const req = http.request({
        host: '127.0.0.1', port,
        method: 'GET', path: target,
        headers: { host: new URL(target).host },
      });
      req.on('response', (res) => { res.resume(); res.on('end', resolve); });
      req.on('error', reject);
      req.end();
    });
  }

  const r = await directGet(port, '/_proxy/log.json');
  const events = JSON.parse(r.body);
  assert.equal(events.length, 3);

  const byOrigin = Object.fromEntries(events.map((e) => [e.origin, e.failureCategory]));
  for (const [target, expected] of targets) {
    const origin = new URL(target).origin;
    assert.equal(byOrigin[origin], expected, `${origin} should be tagged ${expected}`);
  }
  assert.equal(new Set(Object.values(byOrigin)).size, 3);
});

// 4xx keeps the fail-open behaviour: request is forwarded and no block event is recorded.
test('4xx robots.txt: request allowed and no block event (and no failure category) is produced', async (t) => {
  const origin = http.createServer((req, res) => {
    if (req.url === '/robots.txt') {
      res.writeHead(404, { 'content-type': 'text/plain' });
      return res.end('not found');
    }
    res.writeHead(200, { 'content-type': 'text/plain' });
    res.end('PAGE ' + req.url);
  });
  const originPort = await listen(origin);
  t.after(() => origin.close());

  // Point robots fetching at the same local origin so the 404 is served locally.
  const fetchImpl = async (url) => {
    const u = new URL(url);
    return new Promise((resolve, reject) => {
      const r = http.request({ host: u.hostname, port: u.port || 80, path: u.pathname }, (resp) => {
        let body = '';
        resp.on('data', (d) => (body += d));
        resp.on('end', () => resolve({
          ok: resp.statusCode >= 200 && resp.statusCode < 300,
          status: resp.statusCode,
          headers: { get: () => null },
          text: async () => body,
        }));
      });
      r.on('error', reject);
      r.end();
    });
  };

  const config = loadConfig({ HTTPS_MODE: 'host-only' });
  const { server } = await createProxy({ config, fetchImpl, logger: silent });
  const port = await listen(server);
  t.after(() => server.close());

  const r = await new Promise((resolve, reject) => {
    const req = http.request({
      host: '127.0.0.1', port,
      method: 'GET', path: `http://127.0.0.1:${originPort}/anything`,
      headers: { host: `127.0.0.1:${originPort}` },
    });
    req.on('response', (res) => {
      let body = '';
      res.on('data', (d) => (body += d));
      res.on('end', () => resolve({ status: res.statusCode, body }));
    });
    req.on('error', reject);
    req.end();
  });
  assert.equal(r.status, 200);
  assert.equal(r.body, 'PAGE /anything');

  const log = await directGet(port, '/_proxy/log.json');
  assert.deepEqual(JSON.parse(log.body), []);
});

// A rule-based block (2xx robots.txt with Disallow) must leave failureCategory empty.
test('normal robots-disallow block: failureCategory is null in the log event', async (t) => {
  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    headers: { get: () => null },
    text: async () => 'User-agent: *\nDisallow: /private/\n',
  });
  const config = loadConfig({ HTTPS_MODE: 'host-only' });
  const { server } = await createProxy({ config, fetchImpl, logger: silent });
  const port = await listen(server);
  t.after(() => server.close());

  await new Promise((resolve, reject) => {
    const req = http.request({
      host: '127.0.0.1', port,
      method: 'GET', path: 'http://rules.test/private/secret',
      headers: { host: 'rules.test' },
    });
    req.on('response', (res) => { res.resume(); res.on('end', resolve); });
    req.on('error', reject);
    req.end();
  });

  const r = await directGet(port, '/_proxy/log.json');
  const events = JSON.parse(r.body);
  assert.equal(events.length, 1);
  assert.equal(events[0].reason, 'robots-disallow');
  assert.equal(events[0].failureCategory, null);
});
