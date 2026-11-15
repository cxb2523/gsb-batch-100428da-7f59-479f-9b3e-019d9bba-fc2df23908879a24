/* End-to-end test: drives headless Chrome over the DevTools Protocol
   (zero third-party dependencies) against the generated obfuscated pages.

   Covers:
   1. all-success      — every script runs, counters all green
   2. external-fail    — a 404 external script is recorded and later scripts still run
   3. inline-throw     — a throwing inline script is caught and later scripts still run

   Also verifies the report is queryable three ways (window global, hidden DOM
   node, DOM event), head-tag allowlist behavior and body injection are intact,
   and that no failure surfaces as an uncaught runtime exception. */
'use strict';

const http = require('http');
const os = require('os');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const { generateAll } = require('./gen-pages');
const { start: startServer } = require('./server');

const REPO_ROOT = path.join(__dirname, '..', '..');
const SAMPLES_DIR = path.join(__dirname, '..', 'samples');
const WWW_DIR = path.join(__dirname, '..', 'www');
const WORK_ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'rebuild-tests-'));
const PROFILE_ROOT = path.join(WORK_ROOT, 'profile');

/* Windows keeps file handles briefly after Chrome exits; retry instead of failing. */
function rmrf(target) {
  for (let attempt = 0; attempt < 5; attempt++) {
    try { fs.rmSync(target, { recursive: true, force: true }); return; }
    catch (_e) { if (attempt === 4) return; /* best effort */ }
    const waitUntil = Date.now() + 300;
    while (Date.now() < waitUntil) { /* busy-wait: test-only, short */ }
  }
}

/* Chromium refuses remote debugging unless it can resolve the *default*
   user-data-dir; in sandboxed Windows setups the AppData dirs may not exist. */
function ensureBrowserHome() {
  if (process.platform !== 'win32') return;
  const profile = process.env.USERPROFILE || process.env.HOME;
  if (profile) {
    for (const sub of ['AppData/Local', 'AppData/Roaming']) {
      fs.mkdirSync(path.join(profile, sub), { recursive: true });
    }
  }
  if (process.env.LOCALAPPDATA) fs.mkdirSync(process.env.LOCALAPPDATA, { recursive: true });
  if (process.env.APPDATA) fs.mkdirSync(process.env.APPDATA, { recursive: true });
}

const READY_EXPR = `(function(){
  var r = window.__rebuildScriptReport__ || null;
  var node = document.getElementById('__rebuild_script_report__');
  return {
    ready: !!(r && r.results && r.results.length === r.total),
    report: r,
    nodeText: node ? node.textContent : null,
    eventDetail: window.__lastReportEvent || null,
    order: window.__order || null,
    marker: (document.getElementById('marker') || {}).textContent || null,
    title: document.title,
    headCounts: {
      meta: document.head.querySelectorAll('meta').length,
      link: document.head.querySelectorAll('link').length,
      base: document.head.querySelectorAll('base').length,
      style: document.head.querySelectorAll('style').length,
      iframe: document.head.querySelectorAll('iframe').length
    },
    bodyHasMarker: !!document.getElementById('marker')
  };
})()`;

function findBrowser() {
  const candidates = [
    process.env.CHROME_BIN,
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  ].filter(Boolean);
  for (const p of candidates) {
    try { if (fs.existsSync(p)) return p; } catch (_e) {}
  }
  throw new Error('No Chrome/Edge binary found; set CHROME_BIN to override.');
}

function waitForFile(file, timeoutMs) {
  const t0 = Date.now();
  return new Promise((resolve, reject) => {
    (function poll() {
      try { return resolve(fs.readFileSync(file, 'utf8')); } catch (_e) {}
      if (Date.now() - t0 > timeoutMs) reject(new Error('timeout waiting for ' + file));
      else setTimeout(poll, 100);
    })();
  });
}

function httpJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let body = '';
      res.on('data', (d) => (body += d));
      res.on('end', () => {
        try { resolve(JSON.parse(body)); } catch (e) { reject(e); }
      });
    }).on('error', reject);
  });
}

class CdpClient {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.nextId = 0;
    this.pending = new Map();
    this.exceptions = [];
  }

  connect() {
    return new Promise((resolve, reject) => {
      this.ws.addEventListener('open', () => resolve());
      this.ws.addEventListener('error', (e) => reject(e.error || new Error('websocket error')));
      this.ws.addEventListener('message', (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.id && this.pending.has(msg.id)) {
          const p = this.pending.get(msg.id);
          this.pending.delete(msg.id);
          msg.error ? p.reject(new Error(msg.error.message)) : p.resolve(msg.result);
        } else if (msg.method === 'Runtime.exceptionThrown') {
          this.exceptions.push(msg.params.exceptionDetails);
        }
      });
    });
  }

  send(method, params = {}) {
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  evaluate(expression) {
    return this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
      .then((r) => {
        if (r.exceptionDetails) throw new Error('evaluate failed: ' + JSON.stringify(r.exceptionDetails));
        return r.result.value;
      });
  }

  close() {
    try { this.ws.close(); } catch (_e) {}
  }
}

async function launchBrowser(browserBin, profileDir) {
  fs.mkdirSync(profileDir, { recursive: true });
  const args = [
    '--headless=new',
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-default-apps',
    '--disable-extensions',
    '--disable-popup-blocking',
    '--remote-debugging-port=0',
    '--user-data-dir=' + profileDir,
    'about:blank',
  ];
  const child = spawn(browserBin, args, { stdio: ['ignore', 'pipe', 'pipe'] });
  let stderr = '';
  child.stderr.on('data', (d) => { stderr += d.toString(); });
  const portFile = path.join(profileDir, 'DevToolsActivePort');
  const contents = await waitForFile(portFile, 20000);
  const port = contents.split(/\r?\n/)[0].trim();
  const targets = await httpJson('http://127.0.0.1:' + port + '/json/list');
  const page = targets.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
  if (!page) throw new Error('no page target. stderr=' + stderr.slice(-500));
  const cdp = new CdpClient(page.webSocketDebuggerUrl);
  await cdp.connect();
  await cdp.send('Runtime.enable');
  await cdp.send('Page.enable');
  return { child, cdp, port };
}

async function runPage(cdp, url, timeoutMs) {
  cdp.exceptions.length = 0;
  await cdp.send('Page.navigate', { url });
  const deadline = Date.now() + timeoutMs;
  let state = null;
  while (Date.now() < deadline) {
    state = await cdp.evaluate(READY_EXPR);
    if (state.ready) return state;
    await new Promise((r) => setTimeout(r, 200));
  }
  const err = new Error('page did not finish within ' + timeoutMs + 'ms');
  err.state = state;
  throw err;
}

/* ---------------- assertions ---------------- */

let failures = 0;
function check(cond, label) {
  if (cond) {
    console.log('  PASS ' + label);
  } else {
    failures++;
    console.log('  FAIL ' + label);
  }
}
function eq(actual, expected, label) {
  check(JSON.stringify(actual) === JSON.stringify(expected),
    label + ' (expected ' + JSON.stringify(expected) + ', got ' + JSON.stringify(actual) + ')');
}

function commonChecks(state, expected) {
  const r = state.report;
  check(r && typeof r === 'object', 'report exposed at window.__rebuildScriptReport__');
  eq(r.total, expected.total, 'report.total');
  eq(r.succeeded, expected.succeeded, 'report.succeeded');
  eq(r.failed, expected.failed, 'report.failed');
  eq(r.failures.length, expected.failed, 'failures array length');
  eq(r.results.map((x) => x.index), expected.results.map((_, i) => i), 'result indices are sequential');
  eq(r.results.map((x) => x.status), expected.results, 'per-script statuses in order');

  let parsedNode = null;
  try { parsedNode = JSON.parse(state.nodeText); } catch (_e) {}
  check(parsedNode !== null, 'report queryable from hidden DOM node #__rebuild_script_report__');
  check(parsedNode && JSON.stringify(parsedNode) === JSON.stringify(r), 'DOM node report matches window report');

  check(state.eventDetail !== null, 'report dispatched via __rebuildScriptReport__ DOM event');
  check(state.eventDetail && JSON.stringify(state.eventDetail) === JSON.stringify(r),
    'final event detail equals final report');

  eq(state.title, expected.title, 'document.title rebuilt from payload');
  eq(state.marker, 'done', 'body injected and last inline script mutated #marker');
  check(state.bodyHasMarker, 'payload body content is present');
  eq(state.headCounts, expected.headCounts, 'head allowlist kept (meta/link/base/style) and iframe stripped');

  check(cdpExceptionCount === 0, 'no uncaught runtime exceptions escaped the chain');
}

let cdpExceptionCount = 0;

const SCENARIOS = {
  'all-success.html': {
    total: 5, succeeded: 5, failed: 0,
    results: ['success', 'success', 'success', 'success', 'success'],
    order: ['a1', 'inline-1', 'a2', 'inline-2'],
    title: 'all-success-case',
    headCounts: { meta: 3, link: 1, base: 1, style: 1, iframe: 0 },
  },
  'external-fail.html': {
    total: 6, succeeded: 5, failed: 1,
    results: ['success', 'success', 'success', 'failed', 'success', 'success'],
    order: ['b1', 'b-inline-1', 'b2', 'b-inline-2'],
    title: 'external-fail-case',
    headCounts: { meta: 2, link: 0, base: 0, style: 0, iframe: 0 },
    failure: { index: 3, type: 'external', srcPart: 'missing-404.js', messagePart: 'missing-404.js' },
  },
  'inline-throw.html': {
    total: 5, succeeded: 4, failed: 1,
    results: ['success', 'success', 'failed', 'success', 'success'],
    order: ['c1', 'before-throw', 'c2', 'c-inline-2'],
    title: 'inline-throw-case',
    headCounts: { meta: 2, link: 0, base: 0, style: 0, iframe: 0 },
    failure: { index: 2, type: 'inline', srcPart: '', messagePart: 'boom-inline-xyz' },
  },
};
(async () => {
  ensureBrowserHome();
  const browserBin = findBrowser();
  console.log('browser: ' + browserBin);

  /* 1. (re)generate pages from the real index.html shell */
  const shell = fs.readFileSync(path.join(REPO_ROOT, 'index.html'), 'utf8');
  const names = generateAll(shell, SAMPLES_DIR);
  console.log('generated pages: ' + names.join(', '));

  /* 2. static server: samples + external scripts under one root */
  const wwwRoot = path.join(WORK_ROOT, 'www-root');
  rmrf(wwwRoot);
  fs.mkdirSync(path.join(wwwRoot, 'ext'), { recursive: true });
  fs.cpSync(SAMPLES_DIR, wwwRoot, { recursive: true });
  fs.cpSync(WWW_DIR, wwwRoot, { recursive: true });
  const { server, port } = await startServer(wwwRoot);
  console.log('test server: http://127.0.0.1:' + port);

  const profileDir = path.join(PROFILE_ROOT, 'run-' + process.pid);
  let session;
  try {
    session = await launchBrowser(browserBin, profileDir);

    for (const [pageName, expected] of Object.entries(SCENARIOS)) {
      console.log('\n== ' + pageName + ' ==');
      const cdp = session.cdp;
      cdpExceptionCount = 0;
      const state = await runPage(cdp, 'http://127.0.0.1:' + port + '/' + pageName, 20000);
      cdpExceptionCount = cdp.exceptions.length;
      if (cdpExceptionCount) {
        console.log('  uncaught exceptions: ' + JSON.stringify(cdp.exceptions.map((e) => e.text)));
      }
      commonChecks(state, expected);
      eq(state.order, expected.order, 'execution order (proves scripts after the failure still ran)');

      if (expected.failure) {
        const f = state.report.failures[0];
        check(!!f, 'failure detail recorded');
        if (f) {
          eq(f.index, expected.failure.index, 'failure.index');
          eq(f.type, expected.failure.type, 'failure.type (' + expected.failure.type + ')');
          if (expected.failure.type === 'external') {
            check((f.src || '').indexOf(expected.failure.srcPart) !== -1,
              'failure.src contains "' + expected.failure.srcPart + '" (got: ' + f.src + ')');
          } else {
            eq(f.src || '', '', 'inline failure has empty src');
          }
          check((f.message || '').indexOf(expected.failure.messagePart) !== -1,
            'failure.message contains "' + expected.failure.messagePart + '" (got: ' + f.message + ')');
        }
      }
    }

    /* 3. regression contrast: the ORIGINAL runner records nothing at all.
       Whether its chain happens to continue in this browser build or not,
       the decisive gap is that no report / failure detail is ever exposed. */
    console.log('\n== regression check against original index.html (via git show HEAD) ==');
    const { execFileSync } = require('child_process');
    let legacyDir = null;
    try {
      const oldShell = execFileSync('git', ['show', 'HEAD:index.html'], { cwd: REPO_ROOT, encoding: 'utf8' });
      legacyDir = path.join(PROFILE_ROOT, 'legacy-pages');
      rmrf(legacyDir);
      generateAll(oldShell, legacyDir);
      const legacyRoot = path.join(WORK_ROOT, 'legacy-root');
      rmrf(legacyRoot);
      fs.mkdirSync(path.join(legacyRoot, 'ext'), { recursive: true });
      fs.cpSync(legacyDir, legacyRoot, { recursive: true });
      fs.cpSync(WWW_DIR, legacyRoot, { recursive: true });
      const legacy = await startServer(legacyRoot);
      try {
        for (const pageName of ['external-fail.html', 'inline-throw.html']) {
          let state = null;
          try {
            state = await runPage(session.cdp, 'http://127.0.0.1:' + legacy.port + '/' + pageName, 4000);
          } catch (e) { state = e.state; }
          check(state && state.report === null,
            pageName + ': original runner exposes no window.__rebuildScriptReport__');
          check(state && state.nodeText === null,
            pageName + ': original runner writes no report DOM node (failures are invisible)');
          check(state && state.eventDetail === null,
            pageName + ': original runner dispatches no report event');
        }
      } finally {
        await new Promise((r) => legacy.server.close(r));
      }
    } catch (e) {
      console.log('  SKIP legacy contrast (' + e.message + ')');
    }
  } finally {
    if (session) {
      try { await session.cdp.send('Browser.close'); } catch (_e) {}
      session.cdp.close();
      const exit = new Promise((r) => session.child.once('exit', r));
      await Promise.race([exit, new Promise((r) => setTimeout(r, 5000))]);
      if (session.child.exitCode === null) session.child.kill();
    }
    await new Promise((r) => server.close(r));
    rmrf(WORK_ROOT);
  }

  console.log('\n' + (failures === 0 ? 'ALL TESTS PASSED' : failures + ' CHECK(S) FAILED'));
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => {
  console.error('test harness error:', e);
  process.exit(2);
});
