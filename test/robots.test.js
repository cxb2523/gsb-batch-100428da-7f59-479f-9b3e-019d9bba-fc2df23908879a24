import { test } from 'node:test';
import assert from 'node:assert/strict';
import { RobotsCache } from '../src/robots.js';

const config = { cacheTtlMs: 60_000, cacheMax: 1000, robotsTimeoutMs: 5000, robotsUa: null };

function res(status, body = '') {
  return { ok: status >= 200 && status < 300, status, text: async () => body };
}

// Builds a fetchImpl backed by a route map, counting calls per URL.
function fakeFetch(routes) {
  const calls = {};
  const fn = async (url) => {
    calls[url] = (calls[url] || 0) + 1;
    const r = routes[url];
    if (!r) return res(404);
    if (r.abort) {
      // Mirrors what fetch throws when the AbortController timer fires.
      const err = new Error('The operation was aborted');
      err.name = 'AbortError';
      throw err;
    }
    if (r.throw) throw new Error('network down');
    return res(r.status, r.body);
  };
  fn.calls = calls;
  return fn;
}

test('2xx robots.txt: allows and disallows by path', async () => {
  const robots = '#\nUser-agent: *\nDisallow: /private/\n';
  const fetchImpl = fakeFetch({ 'http://x.test/robots.txt': { status: 200, body: robots } });
  const cache = new RobotsCache({ config, fetchImpl });

  const ok = await cache.decide('http://x.test/public/page', 'Bot');
  assert.equal(ok.allowed, true);

  const blocked = await cache.decide('http://x.test/private/secret', 'Bot');
  assert.equal(blocked.allowed, false);
  assert.equal(blocked.reason, 'robots-disallow');
});

test('4xx robots.txt -> allow all (fail open)', async () => {
  const fetchImpl = fakeFetch({ 'http://x.test/robots.txt': { status: 404 } });
  const cache = new RobotsCache({ config, fetchImpl });
  const d = await cache.decide('http://x.test/anything', 'Bot');
  assert.equal(d.allowed, true);
  assert.equal(d.reason, 'robots-absent');
});

test('5xx robots.txt -> block all (fail closed), classified http-5xx', async () => {
  const fetchImpl = fakeFetch({ 'http://x.test/robots.txt': { status: 503 } });
  const cache = new RobotsCache({ config, fetchImpl });
  const d = await cache.decide('http://x.test/anything', 'Bot');
  assert.equal(d.allowed, false);
  assert.equal(d.reason, 'robots-unavailable');
  assert.equal(d.failureCategory, 'http-5xx');

  // The category is persisted on the cache entry too.
  const [entry] = cache.getAll();
  assert.equal(entry.policy, 'block');
  assert.equal(entry.failureCategory, 'http-5xx');
});

test('timeout (AbortError) -> block all, classified timeout', async () => {
  const fetchImpl = fakeFetch({ 'http://x.test/robots.txt': { abort: true } });
  const cache = new RobotsCache({ config, fetchImpl });
  const d = await cache.decide('http://x.test/anything', 'Bot');
  assert.equal(d.allowed, false);
  assert.equal(d.reason, 'robots-unavailable');
  assert.equal(d.failureCategory, 'timeout');
  assert.equal(cache.getAll()[0].failureCategory, 'timeout');
});

test('network error -> block all, classified network-error', async () => {
  const fetchImpl = fakeFetch({ 'http://x.test/robots.txt': { throw: true } });
  const cache = new RobotsCache({ config, fetchImpl });
  const d = await cache.decide('http://x.test/anything', 'Bot');
  assert.equal(d.allowed, false);
  assert.equal(d.reason, 'robots-unavailable');
  assert.equal(d.failureCategory, 'network-error');
  assert.equal(cache.getAll()[0].failureCategory, 'network-error');
});

test('failure categories differ across 5xx, timeout and network error', async () => {
  const cases = [
    ['http://a.test/robots.txt', { status: 500 }, 'http-5xx'],
    ['http://b.test/robots.txt', { abort: true }, 'timeout'],
    ['http://c.test/robots.txt', { throw: true }, 'network-error'],
  ];
  const fetchImpl = fakeFetch(Object.fromEntries(cases.map(([url, r]) => [url, r])));
  const cache = new RobotsCache({ config, fetchImpl });

  for (const [robotsUrl, , expected] of cases) {
    const origin = new URL(robotsUrl).origin;
    const d = await cache.decide(`${origin}/page`, 'Bot');
    assert.equal(d.allowed, false, `${origin} should be blocked`);
    assert.equal(d.failureCategory, expected, `${origin} category`);
  }

  const categories = new Set(cache.getAll().map((e) => e.failureCategory));
  assert.equal(categories.size, 3);
});

test('4xx and 2xx carry no failure category', async () => {
  const fetchImpl = fakeFetch({
    'http://absent.test/robots.txt': { status: 404 },
    'http://ok.test/robots.txt': { status: 200, body: 'User-agent: *\nDisallow: /private/\n' },
  });
  const cache = new RobotsCache({ config, fetchImpl });

  const absent = await cache.decide('http://absent.test/x', 'Bot');
  assert.equal(absent.allowed, true);
  assert.equal(absent.failureCategory, null);

  const ok = await cache.decide('http://ok.test/private/x', 'Bot');
  assert.equal(ok.allowed, false);
  assert.equal(ok.reason, 'robots-disallow');
  assert.equal(ok.failureCategory, null);

  for (const entry of cache.getAll()) {
    assert.equal(entry.failureCategory, null);
  }
});

test('manual override entries expose no fetch failure information', async () => {
  const fetchImpl = fakeFetch({ 'http://x.test/robots.txt': { status: 503 } });
  const cache = new RobotsCache({ config, fetchImpl });

  // A failed fetch first leaves a classified block entry.
  const failed = await cache.decide('http://x.test/a', 'Bot');
  assert.equal(failed.failureCategory, 'http-5xx');

  // An override replaces it and must not carry any failure category at all.
  cache.override('http://x.test', 'User-agent: *\nDisallow: /private/\n');
  const [entry] = cache.getAll();
  assert.equal(entry.isOverride, true);
  assert.equal('failureCategory' in entry, false);

  const d = await cache.decide('http://x.test/private/b', 'Bot');
  assert.equal(d.allowed, false);
  assert.equal(d.reason, 'robots-disallow');
  assert.equal(d.failureCategory, null);
});

test('results are cached per origin (one fetch for many requests)', async () => {
  const fetchImpl = fakeFetch({
    'http://x.test/robots.txt': { status: 200, body: 'User-agent: *\nDisallow: /no\n' },
  });
  const cache = new RobotsCache({ config, fetchImpl });
  await cache.decide('http://x.test/a', 'Bot');
  await cache.decide('http://x.test/b', 'Bot');
  await cache.decide('http://x.test/no', 'Bot');
  assert.equal(fetchImpl.calls['http://x.test/robots.txt'], 1);
});

test('cache evicts beyond cacheMax', async () => {
  const fetchImpl = fakeFetch({
    'http://a.test/robots.txt': { status: 200, body: '' },
    'http://b.test/robots.txt': { status: 200, body: '' },
  });
  const cache = new RobotsCache({ config: { ...config, cacheMax: 1 }, fetchImpl });
  await cache.decide('http://a.test/x', 'Bot');
  await cache.decide('http://b.test/x', 'Bot'); // evicts a.test
  await cache.decide('http://a.test/x', 'Bot'); // refetch
  assert.equal(fetchImpl.calls['http://a.test/robots.txt'], 2);
});

test('ROBOTS_UA override takes precedence over request UA', async () => {
  // Disallow only Googlebot; request UA differs but override forces Googlebot.
  const robots = 'User-agent: Googlebot\nDisallow: /\n\nUser-agent: *\nAllow: /\n';
  const fetchImpl = fakeFetch({ 'http://x.test/robots.txt': { status: 200, body: robots } });
  const cache = new RobotsCache({
    config: { ...config, robotsUa: 'Googlebot' },
    fetchImpl,
  });
  const d = await cache.decide('http://x.test/page', 'Mozilla/5.0');
  assert.equal(d.allowed, false);
});
