'use strict';

// Black-box verification for update.js.
//
// A local HTTP server acts as a fake static news source. Each run spawns
// `node update.js` against an isolated temporary artifact directory, so no
// real repository artifact (latest.json / rss.xml / data/**) is ever touched.
//
// Covers: success, same-date RSS dedup, upstream missing, invalid format,
// HTTP fetch failure (with 3 retries), timeout, and today->yesterday fallback.

const { test, before, after, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const moment = require('moment');

const REPO_ROOT = path.resolve(__dirname, '..');
const UPDATE_SCRIPT = path.join(REPO_ROOT, 'update.js');

const today = moment().utcOffset(8).format('YYYY-MM-DD');
const yesterday = moment().subtract(1, 'day').utcOffset(8).format('YYYY-MM-DD');

function validPayload(date) {
  return {
    date,
    news: [`测试新闻一（${date}）`, '测试新闻二：包含特殊字符 <>&"\'', '测试新闻三'],
  };
}

// --- Fake static source -----------------------------------------------------

let server;
let baseUrl;
let currentHandler;
let currentCounters;

function handleRequest(req, res) {
  const match = /^\/(\d{4}-\d{2}-\d{2})\.json$/.exec(new URL(req.url, baseUrl).pathname);
  const date = match ? match[1] : null;
  currentCounters.requests += 1;
  if (date) {
    currentCounters.byDate[date] = (currentCounters.byDate[date] || 0) + 1;
  }
  return currentHandler({ date, url: req.url, req, res });
}

before(async () => {
  server = http.createServer(handleRequest);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  await new Promise((resolve) => server.close(resolve));
});

// --- Per-test helpers -------------------------------------------------------

let tmpDir;

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'news-collector-'));
  currentCounters = { requests: 0, byDate: {} };
  currentHandler = () => {
    throw new Error('no handler configured');
  };
});

function setHandler(fn) {
  currentHandler = fn;
}

function jsonResponse(res, statusCode, payload) {
  res.writeHead(statusCode, { 'Content-Type': 'text/plain; charset=utf-8' });
  res.end(typeof payload === 'string' ? payload : JSON.stringify(payload));
}

function runCollector(extraEnv = {}) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [UPDATE_SCRIPT], {
      cwd: tmpDir,
      env: {
        ...process.env,
        STATIC_DATA_URL: baseUrl,
        NEWS_DATA_DIR: tmpDir,
        // Keep retry waits tiny while preserving the 3-attempt default.
        FETCH_RETRIES: '3',
        RETRY_DELAY_BASE_MS: '10',
        FETCH_TIMEOUT_MS: '1000',
        ...extraEnv,
      },
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString();
    });
    child.on('close', (code) => resolve({ code, stdout, stderr }));
  });
}

function parseOutcome(stdout) {
  const marker = 'COLLECT_OUTCOME ';
  const idx = stdout.indexOf(marker);
  assert.ok(idx !== -1, `structured outcome missing from stdout:\n${stdout}`);
  const lineStart = idx + marker.length;
  const lineEnd = stdout.indexOf('\n', lineStart);
  // rlog-js colorizes console output, including tokens inside the JSON string.
  const jsonText = stdout
    .slice(lineStart, lineEnd === -1 ? undefined : lineEnd)
    .replace(/\x1b\[[0-9;]*m/g, '')
    .trim();
  return JSON.parse(jsonText);
}

function findLogFile() {
  const logDir = path.join(tmpDir, 'logs');
  assert.ok(fs.existsSync(logDir), 'logs directory should be created');
  const files = fs.readdirSync(logDir).filter((name) => name.endsWith('.log'));
  assert.equal(files.length, 1, 'exactly one run log file expected');
  return path.join(logDir, files[0]);
}

function dateFilePath(date) {
  const [y, m, d] = date.split('-');
  return path.join(tmpDir, 'data', y, m, `${d}.json`);
}

function writeSentinels() {
  const files = {
    'latest.json': 'SENTINEL-LATEST',
    'rss.xml': 'SENTINEL-RSS',
  };
  for (const [name, content] of Object.entries(files)) {
    fs.writeFileSync(path.join(tmpDir, name), content);
  }
  const historyPath = dateFilePath(yesterday);
  fs.mkdirSync(path.dirname(historyPath), { recursive: true });
  fs.writeFileSync(historyPath, 'SENTINEL-HISTORY');
  return { latest: path.join(tmpDir, 'latest.json'), rss: path.join(tmpDir, 'rss.xml'), history: historyPath };
}

// --- Tests ------------------------------------------------------------------

test('成功路径：当天有效数据落盘，latest 与日期文件一致，RSS 生成，结局写日志', async () => {
  setHandler(({ date, res }) => {
    if (date === today) {
      return jsonResponse(res, 200, validPayload(today));
    }
    return jsonResponse(res, 404, 'not found');
  });

  const { code, stdout } = await runCollector();
  const outcome = parseOutcome(stdout);

  assert.equal(code, 0, 'success path must exit zero');
  assert.equal(outcome.status, 'success');
  assert.equal(outcome.statusText, '成功');
  assert.equal(outcome.adoptedDate, today);
  assert.deepEqual(outcome.attemptedDates, [today, yesterday]);
  assert.equal(outcome.fallback, false);
  assert.ok(outcome.reason, 'reason must be present');
  assert.equal(outcome.artifacts.rssRegenerated, true);

  const expectedFile = dateFilePath(today);
  assert.ok(fs.existsSync(expectedFile), 'date file must be written');
  const latestContent = fs.readFileSync(path.join(tmpDir, 'latest.json'), 'utf8');
  assert.equal(latestContent, fs.readFileSync(expectedFile, 'utf8'), 'latest.json must equal the date file');

  const saved = JSON.parse(latestContent);
  assert.equal(saved.date, today.replaceAll('-', '/'));
  assert.ok(Array.isArray(saved.content) && saved.content.length === 3);

  const rss = fs.readFileSync(path.join(tmpDir, 'rss.xml'), 'utf8');
  assert.ok(rss.includes(`everydaynews-${today.replaceAll('-', '-')}`), 'rss must reference the adopted date');
  assert.ok(rss.includes('&amp;'), 'rss must escape special characters');

  const logText = fs.readFileSync(findLogFile(), 'utf8');
  assert.ok(logText.includes('COLLECT_OUTCOME'), 'structured outcome must be written into the log file');
  assert.ok(logText.includes('"status":"success"'), 'log file must carry the machine-readable status');

  // Only today should have been contacted on the success path.
  assert.equal(currentCounters.byDate[today], 1);
  assert.equal(currentCounters.byDate[yesterday] || 0, 0);
});

test('去重逻辑：同一日期二次运行不重新生成 RSS', async () => {
  setHandler(({ date, res }) => {
    if (date === today) {
      return jsonResponse(res, 200, validPayload(today));
    }
    return jsonResponse(res, 404, 'not found');
  });

  const first = parseOutcome((await runCollector()).stdout);
  assert.equal(first.artifacts.rssRegenerated, true);
  const rssPath = path.join(tmpDir, 'rss.xml');
  const rssBefore = fs.readFileSync(rssPath, 'utf8');
  const mtimeBefore = fs.statSync(rssPath).mtimeMs;

  await new Promise((resolve) => setTimeout(resolve, 1200));

  const secondRun = await runCollector();
  const second = parseOutcome(secondRun.stdout);
  assert.equal(secondRun.code, 0);
  assert.equal(second.artifacts.rssRegenerated, false, 'same date must skip RSS regeneration');
  assert.equal(fs.readFileSync(rssPath, 'utf8'), rssBefore, 'rss.xml must stay untouched');
  assert.equal(fs.statSync(rssPath).mtimeMs, mtimeBefore, 'rss.xml mtime must not change');
});

test('上游缺失：当天与昨天均 404，非零退出且不覆盖任何已有产物', async () => {
  const sentinels = writeSentinels();
  setHandler(({ res }) => jsonResponse(res, 404, 'not found'));

  const { code, stdout } = await runCollector();
  const outcome = parseOutcome(stdout);

  assert.equal(code, 1, 'missing upstream must exit non-zero');
  assert.equal(outcome.status, 'upstream_missing');
  assert.equal(outcome.statusText, '上游缺失');
  assert.equal(outcome.adoptedDate, null);
  assert.deepEqual(outcome.attemptedDates, [today, yesterday]);
  assert.ok(outcome.reason.includes('404'), 'reason should explain the 404s');
  assert.equal(outcome.artifacts, null);

  assert.equal(fs.readFileSync(sentinels.latest, 'utf8'), 'SENTINEL-LATEST');
  assert.equal(fs.readFileSync(sentinels.rss, 'utf8'), 'SENTINEL-RSS');
  assert.equal(fs.readFileSync(sentinels.history, 'utf8'), 'SENTINEL-HISTORY');

  // No new date file may appear anywhere under data/.
  const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(full) : [full];
  });
  assert.deepEqual(walk(path.join(tmpDir, 'data')), [sentinels.history]);

  // 404 is definitive: each date is tried exactly once, no retries.
  assert.equal(currentCounters.byDate[today], 1);
  assert.equal(currentCounters.byDate[yesterday], 1);

  const logText = fs.readFileSync(findLogFile(), 'utf8');
  assert.ok(logText.includes('"status":"upstream_missing"'));
});

test('格式无效：date 非字符串 / news 非数组，按失败处理且不写残缺文件', async () => {
  const sentinels = writeSentinels();
  setHandler(({ date, res }) => {
    if (date === today) {
      return jsonResponse(res, 200, { date: 20261115, news: [] });
    }
    return jsonResponse(res, 200, { date: yesterday, news: 'not-an-array' });
  });

  const { code, stdout } = await runCollector();
  const outcome = parseOutcome(stdout);

  assert.equal(code, 1);
  assert.equal(outcome.status, 'invalid_format');
  assert.equal(outcome.statusText, '格式无效');
  assert.equal(outcome.adoptedDate, null);
  assert.ok(outcome.reason.includes('校验') || outcome.reason.includes('格式'));
  assert.ok(outcome.details.every((d) => d.kind === 'invalid_format'));

  assert.equal(fs.readFileSync(sentinels.latest, 'utf8'), 'SENTINEL-LATEST');
  assert.equal(fs.readFileSync(sentinels.rss, 'utf8'), 'SENTINEL-RSS');
  assert.equal(fs.readFileSync(sentinels.history, 'utf8'), 'SENTINEL-HISTORY');

  const walkAll = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? walkAll(full) : [full];
  });
  const leftovers = walkAll(tmpDir).filter((file) => path.basename(file).endsWith('.tmp'));
  assert.deepEqual(leftovers, [], 'no atomic-write temp file may be left behind');

  // Malformed payloads are retried the full 3 attempts per date.
  assert.equal(currentCounters.byDate[today], 3);
  assert.equal(currentCounters.byDate[yesterday], 3);
});

test('抓取失败：持续 HTTP 500，重试 3 次耗尽后非零退出，产物不动', async () => {
  const sentinels = writeSentinels();
  setHandler(({ res }) => jsonResponse(res, 500, 'server error'));

  const { code, stdout } = await runCollector();
  const outcome = parseOutcome(stdout);

  assert.equal(code, 1);
  assert.equal(outcome.status, 'fetch_failed');
  assert.equal(outcome.statusText, '抓取失败');
  assert.ok(outcome.reason.includes('HTTP 500'));
  assert.equal(currentCounters.requests, 6, 'both dates retried 3 times');

  assert.equal(fs.readFileSync(sentinels.latest, 'utf8'), 'SENTINEL-LATEST');
  assert.equal(fs.readFileSync(sentinels.rss, 'utf8'), 'SENTINEL-RSS');
  assert.equal(fs.readFileSync(sentinels.history, 'utf8'), 'SENTINEL-HISTORY');
});

test('回退成功：当天 404、昨天有效时采用昨天并正常落盘', async () => {
  setHandler(({ date, res }) => {
    if (date === today) {
      return jsonResponse(res, 404, 'not found');
    }
    if (date === yesterday) {
      return jsonResponse(res, 200, validPayload(yesterday));
    }
    return jsonResponse(res, 404, 'not found');
  });

  const { code, stdout } = await runCollector();
  const outcome = parseOutcome(stdout);

  assert.equal(code, 0);
  assert.equal(outcome.status, 'success');
  assert.equal(outcome.adoptedDate, yesterday);
  assert.equal(outcome.fallback, true);
  assert.ok(outcome.reason.includes('回退'));

  const expectedFile = dateFilePath(yesterday);
  assert.ok(fs.existsSync(expectedFile));
  assert.equal(
    fs.readFileSync(path.join(tmpDir, 'latest.json'), 'utf8'),
    fs.readFileSync(expectedFile, 'utf8'),
  );
  assert.ok(fs.readFileSync(path.join(tmpDir, 'rss.xml'), 'utf8').includes(
    `everydaynews-${yesterday.replaceAll('-', '-')}`,
  ));
});

test('当天格式无效但昨天有效：仍可回退成功，结局标注采用日期', async () => {
  setHandler(({ date, res }) => {
    if (date === today) {
      return jsonResponse(res, 200, { date: 123, news: [] });
    }
    if (date === yesterday) {
      return jsonResponse(res, 200, validPayload(yesterday));
    }
    return jsonResponse(res, 404, 'not found');
  });

  const { code, stdout } = await runCollector();
  const outcome = parseOutcome(stdout);

  assert.equal(code, 0);
  assert.equal(outcome.status, 'success');
  assert.equal(outcome.adoptedDate, yesterday);
  assert.equal(outcome.fallback, true);
  assert.equal(outcome.details[0].kind, 'invalid_format', 'today attempt must be recorded as invalid');
});

test('抓取失败：响应超过超时时间，归类为抓取失败并保留错误摘要', async () => {
  const sentinels = writeSentinels();
  setHandler(({ res }) => {
    // Intentionally never respond; the AbortController timeout fires.
    setTimeout(() => res.destroy(), 5000);
  });

  const { code, stdout } = await runCollector({ FETCH_TIMEOUT_MS: '200', RETRY_DELAY_BASE_MS: '1' });
  const outcome = parseOutcome(stdout);

  assert.equal(code, 1);
  assert.equal(outcome.status, 'fetch_failed');
  assert.equal(outcome.adoptedDate, null);
  assert.ok(outcome.details.every((d) => d.attempts === 3), 'timeout must still honor 3 attempts');
  assert.ok(outcome.reason.length > 0);

  assert.equal(fs.readFileSync(sentinels.latest, 'utf8'), 'SENTINEL-LATEST');
  assert.equal(fs.readFileSync(sentinels.rss, 'utf8'), 'SENTINEL-RSS');
}, { timeout: 30000 });
