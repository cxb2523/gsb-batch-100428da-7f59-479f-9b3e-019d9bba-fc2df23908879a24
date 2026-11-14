// Offline failure-handling tests for BaseAPI and its subclasses.
//
// These tests run without internet access. An injection double replaces
// `https.request` so that every API call is served by a local plain-HTTP
// server, which lets us simulate corrupted gzip/deflate bodies, requests
// that hang until the client timeout destroys them, and plain network
// errors, and assert the error tagging added in src/base-api.js.

const test = require('node:test');
const assert = require('node:assert');
const http = require('http');
const https = require('https');
const zlib = require('zlib');
const { CrawlingAPI } = require('../index.js');

const TOKEN = 'offline-test-token';

// --- Injection double: redirect every https.request to the local server ---

let targetPort = null;
const originalHttpsRequest = https.request;

https.request = function (options, callback) {
  const patched = Object.assign({}, options, {
    hostname: '127.0.0.1',
    port: targetPort,
    protocol: 'http:',
  });
  delete patched.host;
  return http.request(patched, callback);
};

test.after(() => {
  https.request = originalHttpsRequest;
});

// --- Local server simulating the different upstream behaviours ---

const server = http.createServer((req, res) => {
  const requestedUrl = new URL(req.url, 'http://127.0.0.1').searchParams.get('url') || '';

  if (requestedUrl.includes('corrupted-gzip')) {
    res.writeHead(200, { 'Content-Encoding': 'gzip', 'Content-Type': 'text/html' });
    res.end(Buffer.from('this is not a gzip stream'));
    return;
  }

  if (requestedUrl.includes('corrupted-deflate')) {
    res.writeHead(200, { 'Content-Encoding': 'deflate', 'Content-Type': 'text/html' });
    res.end(Buffer.from('this is not a deflate stream'));
    return;
  }

  if (requestedUrl.includes('hang')) {
    // Never respond: the client-side timeout must destroy the request.
    return;
  }

  if (requestedUrl.includes('ok-json')) {
    const body = zlib.gzipSync(
      JSON.stringify({ url: 'https://example.com/ok-json', original_status: 200, pc_status: 200 }),
    );
    res.writeHead(200, { 'Content-Encoding': 'gzip', 'Content-Type': 'application/json' });
    res.end(body);
    return;
  }

  // Default: a healthy gzipped html response with Crawlbase status headers.
  res.writeHead(200, {
    'Content-Encoding': 'gzip',
    'Content-Type': 'text/html',
    original_status: '200',
    cb_status: '200',
  });
  res.end(zlib.gzipSync('hello world'));
});

test.before(async () => {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  targetPort = server.address().port;
});

test.after(async () => {
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
});

// --- Tests ---

test('corrupted gzip body rejects with a tagged compression error and response context', async () => {
  const api = new CrawlingAPI({ token: TOKEN });
  await assert.rejects(
    api.get('https://example.com/corrupted-gzip'),
    (error) => {
      assert.strictEqual(error.type, 'compression');
      assert.strictEqual(error.statusCode, 200);
      assert.strictEqual(error.headers['content-encoding'], 'gzip');
      return true;
    },
    'expected a compression-tagged rejection',
  );
});

test('corrupted deflate body rejects with a tagged compression error and response context', async () => {
  const api = new CrawlingAPI({ token: TOKEN });
  await assert.rejects(
    api.get('https://example.com/corrupted-deflate'),
    (error) => {
      assert.strictEqual(error.type, 'compression');
      assert.strictEqual(error.statusCode, 200);
      assert.strictEqual(error.headers['content-encoding'], 'deflate');
      return true;
    },
    'expected a compression-tagged rejection',
  );
});

test('request destroyed by timeout rejects with a tagged timeout error carrying the configured timeout', async () => {
  const timeout = 150;
  const api = new CrawlingAPI({ token: TOKEN, timeout });
  await assert.rejects(
    api.get('https://example.com/hang'),
    (error) => {
      assert.strictEqual(error.type, 'timeout');
      assert.strictEqual(error.timeout, timeout);
      assert.strictEqual(error.message, 'Request timeout');
      return true;
    },
    'expected a timeout-tagged rejection',
  );
});

test('plain network errors keep their original shape and are not tagged', async () => {
  // Find a port that is guaranteed to be closed.
  const probe = http.createServer();
  await new Promise((resolve) => probe.listen(0, '127.0.0.1', resolve));
  const closedPort = probe.address().port;
  await new Promise((resolve) => probe.close(resolve));

  const previousPort = targetPort;
  targetPort = closedPort;
  try {
    const api = new CrawlingAPI({ token: TOKEN });
    await assert.rejects(
      api.get('https://example.com/unreachable'),
      (error) => {
        assert.strictEqual(error.code, 'ECONNREFUSED');
        assert.strictEqual(error.type, undefined);
        assert.strictEqual(error.timeout, undefined);
        return true;
      },
      'expected the original network error',
    );
  } finally {
    targetPort = previousPort;
  }
});

test('healthy gzipped response still resolves and CrawlingAPI parses status headers', async () => {
  const api = new CrawlingAPI({ token: TOKEN });
  const response = await api.get('https://example.com/ok');
  assert.strictEqual(response.statusCode, 200);
  assert.strictEqual(response.body, 'hello world');
  assert.strictEqual(response.originalStatus, 200);
  assert.strictEqual(response.cbStatus, 200);
  assert.strictEqual(response.pcStatus, 200);
});

test('healthy gzipped JSON response still resolves and CrawlingAPI parses status fields from the body', async () => {
  const api = new CrawlingAPI({ token: TOKEN });
  const response = await api.get('https://example.com/ok-json');
  assert.strictEqual(response.statusCode, 200);
  assert.deepStrictEqual(response.json, { url: 'https://example.com/ok-json', original_status: 200, pc_status: 200 });
  assert.strictEqual(response.originalStatus, 200);
  assert.strictEqual(response.cbStatus, 200);
});
