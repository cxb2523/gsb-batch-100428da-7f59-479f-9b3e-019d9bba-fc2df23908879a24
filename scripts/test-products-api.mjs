/**
 * Acceptance tests for GET /api/products (cursor-paginated feed).
 *
 * Usage:
 *   node scripts/test-products-api.mjs [baseUrl]
 *
 * Covers:
 *   1. Walking the cursor chain loads all 500 products, 20 per batch,
 *      in stable order, ending with hasMore=false and nextCursor=null.
 *   2. Forged / malformed / tampered cursors return 400 INVALID_CURSOR.
 *   3. A validly-signed cursor past the end returns 416 CURSOR_OUT_OF_RANGE.
 *   4. limit validation (default 20, max 20, rejects 0 / 21 / non-numeric).
 *   5. Retry simulation: a failed batch is retried with the SAME cursor,
 *      producing a final list with no duplicates and no gaps.
 */
import { createHmac } from 'node:crypto';

const BASE = process.argv[2] ?? 'http://localhost:3000';
const SECRET =
  process.env.PRODUCTS_CURSOR_SECRET ?? 'scraping-sandbox-products-cursor-v1';

let failures = 0;
function check(name, cond, detail = '') {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures++;
    console.error(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`);
  }
}

async function api(path) {
  const res = await fetch(`${BASE}${path}`);
  const body = await res.json();
  return { status: res.status, body };
}

function sign(offset) {
  return createHmac('sha256', SECRET)
    .update(`products:${offset}`)
    .digest('hex')
    .slice(0, 16);
}

// --- 1. full walk ----------------------------------------------------------
console.log('\n[1] Sequential load of all batches');
{
  const ids = [];
  let cursor = null;
  let batches = 0;
  let last = null;
  for (;;) {
    const qs = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
    const { status, body } = await api(`/api/products${qs}`);
    check(`batch ${batches + 1} returns 200`, status === 200, `got ${status}`);
    if (status !== 200) break;
    check(
      `batch ${batches + 1} has <= 20 items`,
      body.data.length <= 20,
      `got ${body.data.length}`,
    );
    ids.push(...body.data.map((p) => p.id));
    batches++;
    last = body;
    if (!body.hasMore) break;
    if (!body.nextCursor) {
      check('nextCursor present while hasMore=true', false);
      break;
    }
    cursor = body.nextCursor;
    if (batches > 40) break; // safety
  }
  check('total is 500', last?.total === 500, `got ${last?.total}`);
  check('loaded exactly 500 products', ids.length === 500, `got ${ids.length}`);
  check(
    'no duplicates across batches',
    new Set(ids).size === ids.length,
    `${ids.length - new Set(ids).size} duplicates`,
  );
  check(
    'ids are contiguous 1..500 (no gaps)',
    ids.every((id, i) => id === i + 1),
  );
  check('final batch reports hasMore=false', last?.hasMore === false);
  check('final batch reports nextCursor=null', last?.nextCursor === null);
  check('took 25 batches of 20', batches === 25, `got ${batches}`);
}

// --- 2. forged / malformed cursors -----------------------------------------
console.log('\n[2] Forged cursors are rejected');
{
  const valid = (await api('/api/products')).body.nextCursor;
  const cases = [
    ['garbage string', 'abc'],
    ['empty-looking token', 'null'],
    ['valid format, bad signature', '20.0000000000000000'],
    ['offset tampered', valid.replace(/^20\./, '40.')],
    ['signature tampered', valid.replace(/.$/, (c) => (c === 'a' ? 'b' : 'a'))],
    ['negative offset', '-1.0000000000000000'],
  ];
  for (const [name, cursor] of cases) {
    const { status, body } = await api(
      `/api/products?cursor=${encodeURIComponent(cursor)}`,
    );
    check(
      `${name} -> 400 INVALID_CURSOR`,
      status === 400 && body?.error?.code === 'INVALID_CURSOR',
      `got ${status} ${JSON.stringify(body)}`,
    );
  }
}

// --- 3. out-of-range cursor -------------------------------------------------
console.log('\n[3] Out-of-range cursor');
{
  const beyond = `501.${sign(501)}`;
  const { status, body } = await api(
    `/api/products?cursor=${encodeURIComponent(beyond)}`,
  );
  check(
    'offset 501 -> 416 CURSOR_OUT_OF_RANGE',
    status === 416 && body?.error?.code === 'CURSOR_OUT_OF_RANGE',
    `got ${status} ${JSON.stringify(body)}`,
  );
  const atEnd = `500.${sign(500)}`;
  const end = await api(`/api/products?cursor=${encodeURIComponent(atEnd)}`);
  check(
    'offset 500 (exactly at end) -> 200, empty, hasMore=false',
    end.status === 200 &&
      end.body.data.length === 0 &&
      end.body.hasMore === false &&
      end.body.nextCursor === null,
    `got ${end.status} ${JSON.stringify(end.body).slice(0, 120)}`,
  );
}

// --- 4. limit validation ----------------------------------------------------
console.log('\n[4] limit parameter');
{
  const def = await api('/api/products');
  check('default limit is 20', def.body.data.length === 20);
  const five = await api('/api/products?limit=5');
  check('limit=5 returns 5 items', five.body.data.length === 5);
  for (const bad of ['0', '21', 'abc', '-3']) {
    const { status, body } = await api(`/api/products?limit=${bad}`);
    check(
      `limit=${bad} -> 400 INVALID_LIMIT`,
      status === 400 && body?.error?.code === 'INVALID_LIMIT',
      `got ${status}`,
    );
  }
}

// --- 5. retry after a failed batch ------------------------------------------
console.log('\n[5] Retry resumes from the failed batch (no dupes, no gaps)');
{
  // Mirror the client component's algorithm: cursor only advances on
  // success; a failed batch is retried with the cursor it attempted.
  const ids = [];
  const seen = new Set();
  let nextCursor = null;
  let failedCursor;
  const FAIL_AT_BATCH = 3;
  let failedOnce = false;
  let batch = 0;
  let hasMore = true;

  while (hasMore) {
    batch++;
    const cursor = failedCursor !== undefined ? failedCursor : nextCursor;
    failedCursor = undefined;
    let body;
    if (batch === FAIL_AT_BATCH && !failedOnce) {
      // Simulated network/server failure for this batch (once only).
      failedOnce = true;
      failedCursor = cursor;
      batch--; // retry will re-attempt the same logical batch
      continue;
    }
    const qs = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
    const res = await api(`/api/products${qs}`);
    if (res.status !== 200) {
      failedCursor = cursor;
      batch--;
      continue;
    }
    body = res.body;
    for (const p of body.data) {
      if (!seen.has(p.id)) {
        seen.add(p.id);
        ids.push(p.id);
      }
    }
    nextCursor = body.nextCursor;
    hasMore = body.hasMore;
  }
  check('loaded all 500 after retry', ids.length === 500, `got ${ids.length}`);
  check('no duplicates after retry', seen.size === 500);
  check(
    'no gaps after retry (ids 1..500 in order)',
    ids.every((id, i) => id === i + 1),
  );
}

console.log(
  failures === 0 ? '\nAll tests passed.' : `\n${failures} test(s) FAILED.`,
);
process.exit(failures === 0 ? 0 : 1);
