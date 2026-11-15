/**
 * Offline tests for settings/sync_catalog.js.
 *
 * Every run uses a throwaway SYNC_CATALOG_ROOT and local HTTP servers, so the
 * real catalog/ files in the repository are never touched and no network
 * access is required.
 *
 * The sync script is launched asynchronously with child_process.spawn rather
 * than spawnSync: on Windows spawnSync inherits the test process's open
 * server socket handles and can deadlock waiting for them to close.
 *
 * Run with: node --test settings/sync_catalog.test.js
 */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');

const SYNC_SCRIPT = path.join(__dirname, 'sync_catalog.js');
const HEADINGS = ['Apify catalog', 'CoreClaw catalog'];

function startServer(handler) {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => handler(req, res));
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({ url: `http://127.0.0.1:${port}/`, close: () => new Promise((done) => server.close(done)) });
    });
  });
}

function successServer(body) {
  return startServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end(body);
  });
}

function statusServer(statusCode) {
  return startServer((req, res) => {
    res.writeHead(statusCode);
    res.end(`upstream ${statusCode}`);
  });
}

function makeTempRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'sync-catalog-test-'));
}

function runSync(root, envOverrides) {
  return new Promise((resolve, reject) => {
    const child = spawn(
      process.execPath,
      [SYNC_SCRIPT],
      { env: { ...process.env, SYNC_CATALOG_ROOT: root, ...envOverrides } },
    );
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.on('error', reject);
    child.on('exit', (status, signal) => resolve({ status, signal, stdout, stderr }));
  });
}

function parseOutcomes(stdout) {
  const outcomes = {};
  for (const line of stdout.split(/\r?\n/)) {
    const match = line.match(/^(Apify catalog|CoreClaw catalog): (updated|unchanged|kept-existing|failed)(?: \((.*)\))?$/);
    if (match) outcomes[match[1]] = { status: match[2], reason: match[3] || null };
  }
  return outcomes;
}

function stamped(url, body) {
  return `<!-- Synced from ${url} -->\n\n${body.trim()}\n`;
}

test('one source fails without an old file while the other source still syncs and writes', async () => {
  const root = makeTempRoot();
  const failing = await statusServer(500);
  const healthy = await successServer('# CoreClaw upstream body\n\nfresh content\n');
  try {
    const run = await runSync(root, {
      APIFY_SOURCE_URL: failing.url,
      CORECLAW_SOURCE_URL: healthy.url,
    });

    // The failed source had no local fallback, so the overall run is non-zero,
    // yet the healthy source must still have been fetched and written.
    assert.equal(run.status, 1);
    const outcomes = parseOutcomes(run.stdout);
    assert.equal(outcomes['Apify catalog'].status, 'failed');
    assert.ok(outcomes['Apify catalog'].reason, 'failed outcome must carry a reason');
    assert.match(outcomes['Apify catalog'].reason, /500/);
    assert.equal(outcomes['CoreClaw catalog'].status, 'updated');
    assert.equal(outcomes['CoreClaw catalog'].reason, null);

    assert.equal(fs.existsSync(path.join(root, 'catalog', 'README.md')), false);
    assert.equal(
      fs.readFileSync(path.join(root, 'catalog', 'coreclaw.md'), 'utf8'),
      stamped(healthy.url, '# CoreClaw upstream body\n\nfresh content\n'),
    );
  } finally {
    await failing.close();
    await healthy.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('both sources fail with no local files: both reported failed with reasons, exit code non-zero', async () => {
  const root = makeTempRoot();
  const broken = await statusServer(503);
  // A closed server gives a connection-refused style fetch error.
  const refused = await startServer((req, res) => res.end());
  const refusedUrl = refused.url;
  await refused.close();
  try {
    const run = await runSync(root, {
      APIFY_SOURCE_URL: broken.url,
      CORECLAW_SOURCE_URL: refusedUrl,
    });
    assert.equal(run.status, 1);

    const outcomes = parseOutcomes(run.stdout);
    for (const heading of HEADINGS) {
      assert.equal(outcomes[heading].status, 'failed', `${heading} should be failed`);
      assert.ok(outcomes[heading].reason, `${heading} failure must carry a reason`);
    }
    assert.match(outcomes['Apify catalog'].reason, /503/);
    assert.ok(outcomes['CoreClaw catalog'].reason.length > 0);

    assert.equal(fs.existsSync(path.join(root, 'catalog')), false);
  } finally {
    await broken.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('a failing source keeps the existing local file while the other source updates', async () => {
  const root = makeTempRoot();
  const catalogDir = path.join(root, 'catalog');
  fs.mkdirSync(catalogDir, { recursive: true });
  const apifyFile = path.join(catalogDir, 'README.md');
  const oldApifyContent = '<!-- Synced from old-url -->\n\n# old apify catalog that must survive\n';
  fs.writeFileSync(apifyFile, oldApifyContent, 'utf8');

  const broken = await statusServer(404);
  const healthy = await successServer('# CoreClaw body\n');
  try {
    const run = await runSync(root, {
      APIFY_SOURCE_URL: broken.url,
      CORECLAW_SOURCE_URL: healthy.url,
    });

    // A kept-existing failure is not a hard failure for the whole run.
    assert.equal(run.status, 0);
    const outcomes = parseOutcomes(run.stdout);
    assert.equal(outcomes['Apify catalog'].status, 'kept-existing');
    assert.ok(outcomes['Apify catalog'].reason);
    assert.match(outcomes['Apify catalog'].reason, /404/);
    assert.equal(outcomes['CoreClaw catalog'].status, 'updated');
    assert.equal(outcomes['CoreClaw catalog'].reason, null);

    // Old content preserved byte for byte; other source still written.
    assert.equal(fs.readFileSync(apifyFile, 'utf8'), oldApifyContent);
    assert.equal(
      fs.readFileSync(path.join(catalogDir, 'coreclaw.md'), 'utf8'),
      stamped(healthy.url, '# CoreClaw body\n'),
    );
  } finally {
    await broken.close();
    await healthy.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('identical upstream content is reported unchanged and the file is not rewritten', async () => {
  const root = makeTempRoot();
  const catalogDir = path.join(root, 'catalog');
  fs.mkdirSync(catalogDir, { recursive: true });
  const body = '# Same body as before\n';
  const server = await successServer(body);
  const apifyFile = path.join(catalogDir, 'README.md');
  const expected = stamped(server.url, body);
  fs.writeFileSync(apifyFile, expected, 'utf8');
  const mtimeBefore = fs.statSync(apifyFile).mtimeMs;
  try {
    // Give the filesystem clock a chance to advance.
    await new Promise((resolve) => setTimeout(resolve, 120));
    const run = await runSync(root, {
      APIFY_SOURCE_URL: server.url,
      CORECLAW_SOURCE_URL: server.url,
    });
    assert.equal(run.status, 0);
    const outcomes = parseOutcomes(run.stdout);
    assert.equal(outcomes['Apify catalog'].status, 'unchanged');
    assert.equal(outcomes['CoreClaw catalog'].status, 'updated');
    assert.equal(fs.statSync(apifyFile).mtimeMs, mtimeBefore, 'unchanged file must not be rewritten');
    assert.equal(fs.readFileSync(apifyFile, 'utf8'), expected);
  } finally {
    await server.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('a write failure without an old file is reported failed with the reason and a non-zero exit code', async () => {
  const root = makeTempRoot();
  // Block catalog/ with a regular file so mkdirSync/writeFileSync fail for
  // both sources; neither destination file exists beforehand.
  fs.writeFileSync(path.join(root, 'catalog'), 'blocking file', 'utf8');
  const apify = await successServer('# apify\n');
  const coreclaw = await successServer('# coreclaw\n');
  try {
    const run = await runSync(root, {
      APIFY_SOURCE_URL: apify.url,
      CORECLAW_SOURCE_URL: coreclaw.url,
    });
    assert.equal(run.status, 1);
    const outcomes = parseOutcomes(run.stdout);
    for (const heading of HEADINGS) {
      assert.equal(outcomes[heading].status, 'failed');
      assert.ok(outcomes[heading].reason, `${heading} write failure must carry a reason`);
    }
    // The blocking path itself must remain untouched.
    assert.equal(fs.readFileSync(path.join(root, 'catalog'), 'utf8'), 'blocking file');
  } finally {
    await apify.close();
    await coreclaw.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('a write failure with an old file restores it and is reported kept-existing', async (t) => {
  if (process.getuid && process.getuid() === 0) {
    t.skip('read-only permissions are not enforced for the root user');
    return;
  }
  const root = makeTempRoot();
  const catalogDir = path.join(root, 'catalog');
  fs.mkdirSync(catalogDir, { recursive: true });
  const apifyFile = path.join(catalogDir, 'README.md');
  const oldContent = '<!-- Synced from old-url -->\n\n# protected old content\n';
  fs.writeFileSync(apifyFile, oldContent, 'utf8');
  fs.chmodSync(apifyFile, 0o444);

  const server = await successServer('# brand new content that cannot be written\n');
  try {
    const run = await runSync(root, {
      APIFY_SOURCE_URL: server.url,
      CORECLAW_SOURCE_URL: server.url,
    });
    const outcomes = parseOutcomes(run.stdout);
    if (outcomes['Apify catalog'].status === 'updated') {
      // Platform did not enforce the read-only bit (e.g. privileged user);
      // the branch cannot be exercised in this environment.
      t.skip('read-only file was still writable in this environment');
      return;
    }
    assert.equal(outcomes['Apify catalog'].status, 'kept-existing');
    assert.ok(outcomes['Apify catalog'].reason);
    assert.equal(run.status, 0, 'kept-existing is not a hard failure');
    assert.equal(fs.readFileSync(apifyFile, 'utf8'), oldContent);
  } finally {
    fs.chmodSync(apifyFile, 0o666);
    await server.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});
