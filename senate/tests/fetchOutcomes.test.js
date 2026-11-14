const { test, afterEach } = require('node:test');
const assert = require('node:assert/strict');

const {
  fetchAll,
  __setTransportForTests,
} = require('../dist/fetcher/senateFetcher.js');

// ─── A fake Senate eFD site ───────────────────────────────────────────────────
// Every request the fetcher makes is answered here — no real network. Only the
// wire is replaced (__setTransportForTests); the cookie jar, handshake, manual
// redirect walker and parsers are the production ones.

const BASE = 'https://efdsearch.senate.gov';

const HOME_HTML = [
  '<html><head><title>eFD: Home</title></head><body>',
  '<form action="/search/home/" method="post">',
  '<input type="hidden" name="csrfmiddlewaretoken" value="csrf-from-form">',
  '<input type="checkbox" name="prohibition_agreement" value="1">',
  '</form></body></html>',
].join('');

const NORMAL_TABLE_HTML = [
  '<html><head><title>eFD: Report</title></head><body>',
  '<h1>Periodic Transaction Report for 08/05/2026</h1>',
  '<table><tbody>',
  '<tr><td>1</td><td>08/14/2026</td><td>Self</td><td>EA</td>',
  '<td>Electronic Arts Inc.</td><td>Stock</td><td>Purchase</td>',
  '<td>$1,001 - $15,000</td><td></td></tr>',
  '</tbody></table></body></html>',
].join('');

const EMPTY_TABLE_HTML =
  '<html><head><title>eFD: Report</title></head><body><p>No data</p></body></html>';

function response(config, { status = 200, data = '', headers = {} } = {}) {
  return { data, status, statusText: status >= 300 ? 'Found' : 'OK', headers, config, request: {} };
}

function homeResponse(config) {
  return response(config, {
    status: 200,
    data: HOME_HTML,
    headers: { 'content-type': 'text/html', 'set-cookie': ['csrftoken=tok-123; Path=/'] },
  });
}

// One listing row as the DataTables endpoint returns it.
function listingRow({
  docId = '11111111-1111-4111-8111-111111111111',
  docType = 'ptr',
  first = 'Jane',
  last = 'Doe',
  office = 'Senator',
  filedDate = '09/17/2026',
  href = null,
} = {}) {
  const link = href ?? `/search/view/${docType}/${docId}/`;
  return [
    `<a href="/search/filer/">${first}</a>`,
    `<a href="/search/filer/">${last}</a>`,
    office,
    `<a href="${link}">Periodic Transaction Report</a>`,
    filedDate,
  ];
}

/**
 * detailFor(pathname, attempt) decides each detail-page request:
 *   { kind: 'html', html }  → 200 with that HTML
 *   { kind: 'redirect' }    → 302 to /search/home/ (session lost)
 *   { kind: 'throw' }       → network error
 * Defaults to a normal table.
 */
function createSite({ listing, total = listing.length, failPageAtStart = null, detailFor } = {}) {
  const requests = [];
  const detailAttempts = new Map();

  async function adapter(config) {
    const rawUrl = config.url.startsWith('http') ? config.url : `${BASE}${config.url}`;
    const u = new URL(rawUrl);
    const method = (config.method ?? 'get').toUpperCase();
    const body = typeof config.data === 'string' ? config.data : '';
    requests.push({ method, url: rawUrl, pathname: u.pathname, body });

    // ── Handshake ──────────────────────────────────────────────────────────
    if (u.pathname === '/search/home/') {
      if (method === 'POST') {
        return response(config, { status: 302, headers: { location: '/search/' } });
      }
      return homeResponse(config);
    }
    if (u.pathname === '/search/' && method === 'GET') {
      return response(config, { status: 200, data: HOME_HTML, headers: { 'content-type': 'text/html' } });
    }

    // ── Listing (DataTables) ────────────────────────────────────────────────
    if (u.pathname === '/search/report/data/' && method === 'POST') {
      const start = Number(new URLSearchParams(body).get('start') ?? 0);
      if (failPageAtStart !== null && start === failPageAtStart) {
        throw new Error(`listing pagination failed at start=${start}`);
      }
      return response(config, {
        status: 200,
        data: {
          draw: 1,
          recordsTotal: total,
          recordsFiltered: total,
          data: start === 0 ? listing : [],
        },
      });
    }

    // ── Filing detail pages ─────────────────────────────────────────────────
    if (u.pathname.startsWith('/search/view/ptr/') || u.pathname.startsWith('/search/view/paper/')) {
      const attempt = (detailAttempts.get(u.pathname) ?? 0) + 1;
      detailAttempts.set(u.pathname, attempt);

      const spec = detailFor ? detailFor(u.pathname, attempt) : { kind: 'html', html: NORMAL_TABLE_HTML };
      if (spec.kind === 'throw') throw new Error(`detail fetch failed for ${u.pathname}`);
      if (spec.kind === 'redirect') {
        return response(config, { status: 302, headers: { location: '/search/home/' } });
      }
      return response(config, { status: 200, data: spec.html, headers: { 'content-type': 'text/html' } });
    }

    throw new Error(`Unexpected request: ${method} ${rawUrl}`);
  }

  return { adapter, requests, detailAttempts };
}

async function runFetchAll(site) {
  __setTransportForTests(site.adapter);
  return fetchAll('2026-09-01', '2026-09-20', { pace: false });
}

afterEach(() => {
  __setTransportForTests(undefined);
});

function recordsFor(result, filingId) {
  return result.records.filter((r) => r.filing_id === filingId);
}

function outcomeFor(result, filingId) {
  return result.outcomes.find((o) => o.filing_id === filingId);
}

// ─── All five outcome kinds in one run ───────────────────────────────────────

test('fetchAll reports all five outcome counts and one outcome per recognized filing', async () => {
  const okId = 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa';
  const emptyId = 'bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb';
  const paperId = '998877';

  const site = createSite({
    listing: [
      listingRow({ docId: okId }),
      listingRow({ docId: emptyId }),
      listingRow({ docId: paperId, docType: 'paper' }),
      listingRow({ href: '/search/view/annual/xyz/', filedDate: '09/17/2026' }),
      ['Jane', 'Doe'], // too malformed to even read a link
    ],
    detailFor: (pathname) => {
      if (pathname.includes(okId)) return { kind: 'html', html: NORMAL_TABLE_HTML };
      if (pathname.includes(emptyId)) return { kind: 'html', html: EMPTY_TABLE_HTML };
      return { kind: 'throw' };
    },
  });

  const result = await runFetchAll(site);

  assert.equal(result.electronicPtrCount, 1);
  assert.equal(result.paperCount, 1);
  assert.equal(result.emptyPtrCount, 1);
  assert.equal(result.fetchFailedCount, 0);
  assert.equal(result.unknownDocTypeCount, 1);
  assert.equal(result.unknownDocTypeExamples.length, 1);
  assert.equal(result.unknownDocTypeExamples[0], `${BASE}/search/view/annual/xyz/`);

  assert.deepEqual(result.outcomeCounts, {
    electronicPtrCount: 1,
    paperCount: 1,
    emptyPtrCount: 1,
    fetchFailedCount: 0,
    unknownDocTypeCount: 1,
  });

  // Exactly one outcome per recognized filing — the unknown-link row and the
  // malformed row have no recognized filing and therefore no outcome.
  assert.equal(result.outcomes.length, 3);
  assert.equal(outcomeFor(result, okId).outcome, 'electronic_ptr');
  assert.equal(outcomeFor(result, emptyId).outcome, 'empty_ptr');
  assert.equal(outcomeFor(result, paperId).outcome, 'paper');

  assert.equal(result.records.length, 3);
  assert.equal(recordsFor(result, okId)[0].parse_status, 'ok');
  assert.equal(recordsFor(result, emptyId)[0].parse_status, 'parse_failed');
  assert.equal(recordsFor(result, paperId)[0].parse_status, 'scanned_unparsed');

  // The unknown-link row must not be written as a placeholder: every record
  // in this run is accounted for by one of the three recognized filings.
  assert.equal(result.records.length, result.outcomes.length);

  // Paper filing: never fetches a detail page.
  assert.equal(site.requests.some((r) => r.pathname.startsWith('/search/view/paper/')), false);

  assert.equal(result.success, true);
  assert.equal(result.listingComplete, true);
  assert.equal(result.incomplete, false);
});

// ─── Placeholders line up with the listing filing ────────────────────────────

test('placeholder filing_id / pdf_url / fetch_error match the outcome for that filing', async () => {
  const failId = 'cccccccc-3333-4333-8333-cccccccccccc';
  const site = createSite({
    listing: [listingRow({ docId: failId })],
    detailFor: () => ({ kind: 'throw' }),
  });

  const result = await runFetchAll(site);
  const outcome = outcomeFor(result, failId);
  const [placeholder] = recordsFor(result, failId);

  assert.equal(result.fetchFailedCount, 1);
  assert.equal(result.emptyPtrCount, 0, 'a throw must not be miscounted as an empty table');
  assert.equal(result.electronicPtrCount, 0);
  assert.equal(outcome.outcome, 'fetch_failed');
  assert.equal(placeholder.parse_status, 'fetch_failed');
  assert.equal(placeholder.filing_id, failId);
  assert.equal(outcome.filing_id, failId);
  assert.equal(placeholder.raw_json.fetch_error, outcome.error);
  assert.match(placeholder.raw_json.fetch_error, /detail fetch failed/);
  assert.equal(placeholder.pdf_url, outcome.detail_url);
  assert.equal(placeholder.pdf_url, `${BASE}/search/view/ptr/${failId}/`);
  // Exactly one placeholder for the filing, never also a real row.
  assert.equal(recordsFor(result, failId).length, 1);
});

test('a listing row returned twice produces one outcome and one placeholder, not two', async () => {
  const id = 'dddddddd-4444-4444-8444-dddddddddddd';
  const site = createSite({
    listing: [listingRow({ docId: id }), listingRow({ docId: id })],
    detailFor: () => ({ kind: 'html', html: EMPTY_TABLE_HTML }),
  });

  const result = await runFetchAll(site);

  assert.equal(result.outcomes.length, 1);
  assert.equal(result.emptyPtrCount, 1);
  assert.equal(result.records.length, 1, 'one placeholder for the filing');
  assert.equal(result.records.filter((r) => r.parse_status === 'parse_failed').length, 1);
});

// ─── Redirect after re-handshake: retry success ──────────────────────────────

test('a home-page redirect that succeeds after the one re-handshake retry counts as electronic success', async () => {
  const id = 'eeeeeeee-5555-4555-8555-eeeeeeeeeeee';
  const site = createSite({
    listing: [listingRow({ docId: id })],
    detailFor: (_pathname, attempt) =>
      attempt === 1 ? { kind: 'redirect' } : { kind: 'html', html: NORMAL_TABLE_HTML },
  });

  const result = await runFetchAll(site);

  assert.equal(result.electronicPtrCount, 1);
  assert.equal(result.fetchFailedCount, 0, 'the redirect was resolved by the retry');
  assert.equal(result.emptyPtrCount, 0, 'the home page must never parse as an empty table');
  const outcome = outcomeFor(result, id);
  assert.equal(outcome.outcome, 'electronic_ptr');
  assert.equal(outcome.row_count, 1);
  assert.equal(recordsFor(result, id).length, 1);
  assert.equal(recordsFor(result, id)[0].parse_status, 'ok');
  // The detail page was requested twice (initial + retry after re-handshake).
  assert.equal(site.detailAttempts.get(`/search/view/ptr/${id}/`), 2);
});

// ─── Redirect after re-handshake: retry still redirected ─────────────────────

test('a redirect that persists after re-handshake counts as a fetch failure with the error preserved', async () => {
  const id = 'ffffffff-6666-4666-8666-ffffffffffff';
  const site = createSite({
    listing: [listingRow({ docId: id })],
    detailFor: () => ({ kind: 'redirect' }),
  });

  const result = await runFetchAll(site);

  assert.equal(result.fetchFailedCount, 1);
  assert.equal(result.electronicPtrCount, 0);
  assert.equal(result.emptyPtrCount, 0, 'a session redirect must not be counted as an empty parse');

  const outcome = outcomeFor(result, id);
  const [placeholder] = recordsFor(result, id);
  assert.equal(outcome.outcome, 'fetch_failed');
  assert.equal(placeholder.parse_status, 'fetch_failed');
  assert.equal(placeholder.filing_id, id);
  assert.equal(placeholder.pdf_url, outcome.detail_url);
  assert.match(outcome.error, /redirected to the home page/i);
  assert.equal(placeholder.raw_json.fetch_error, outcome.error);
  // Exactly one re-handshake retry: initial attempt + one retry, no more.
  assert.equal(site.detailAttempts.get(`/search/view/ptr/${id}/`), 2);
});

// ─── Listing pagination failure ──────────────────────────────────────────────

test('a pagination failure still fetches and counts the filings already collected, and marks the run incomplete', async () => {
  const a = '12121212-1111-4111-8111-121212121212';
  const b = '34343434-3333-4333-8333-343434343434';
  const site = createSite({
    listing: [listingRow({ docId: a }), listingRow({ docId: b })],
    total: 150, // claims more pages than were returned
    failPageAtStart: 100,
    detailFor: () => ({ kind: 'html', html: NORMAL_TABLE_HTML }),
  });

  const result = await runFetchAll(site);

  assert.equal(result.listingComplete, false);
  assert.equal(result.incomplete, true);
  assert.equal(result.success, false);
  assert.match(result.error, /offset=100/);

  // The two filings collected before the failure were still fetched and
  // counted normally.
  assert.equal(result.electronicPtrCount, 2);
  assert.equal(result.outcomes.length, 2);
  assert.deepEqual(result.outcomes.map((o) => o.filing_id).sort(), [a, b].sort());
  assert.equal(result.records.length, 2);
  assert.equal(site.detailAttempts.get(`/search/view/ptr/${a}/`), 1);
  assert.equal(site.detailAttempts.get(`/search/view/ptr/${b}/`), 1);
});

// ─── Malformed rows are not unknown links ────────────────────────────────────

test('a row with no readable link (missing fields) is not counted as an unknown link shape', async () => {
  const site = createSite({
    listing: [
      ['Jane', 'Doe'],                                     // too few cells
      ['', 'Doe', 'Senator', '', '09/17/2026'],            // empty report cell
      ['Jane', 'Doe', 'Senator', 'Periodic Transaction Report', '09/17/2026'], // text but no href
    ],
  });

  const result = await runFetchAll(site);

  assert.equal(result.unknownDocTypeCount, 0, 'unreadable rows must not inflate the unknown-link count');
  assert.equal(result.unknownDocTypeExamples.length, 0);
  assert.equal(result.outcomes.length, 0);
  assert.equal(result.records.length, 0);
  // A complete but empty listing after filtering is not a failure.
  assert.equal(result.success, true);
  assert.equal(result.listingComplete, true);
});

// ─── RUN_SUMMARY carries the same breakdown ─────────────────────────────────

test('RUN_SUMMARY carries the five counts, the per-filing outcomes and the completeness flags', () => {
  const { buildRunSummary } = require('../dist/utils/runSummary.js');

  const outcomes = [
    {
      filing_id: 'a', politician: 'Jane Doe', filing_date: '09/17/2026',
      doc_type: 'ptr', outcome: 'fetch_failed', detail_url: `${BASE}/search/view/ptr/a/`,
      row_count: 1, source_id: 'a|fetch_failed', error: 'Timeout after 20000ms',
    },
    {
      filing_id: 'b', politician: 'Jane Doe', filing_date: '09/17/2026',
      doc_type: 'paper', outcome: 'paper', detail_url: `${BASE}/search/view/paper/b/`,
      row_count: 1, source_id: 'b|paper',
    },
  ];
  const summary = buildRunSummary({
    electronicPtrCount: 0, paperCount: 1, emptyPtrCount: 0, fetchFailedCount: 1,
    unknownDocTypeCount: 2, unknownDocTypeExamples: ['https://example/annual/'],
    outcomes, listingComplete: false, incomplete: true,
  });

  assert.equal(summary.electronicPtrCount, 0);
  assert.equal(summary.paperCount, 1);
  assert.equal(summary.fetchFailedCount, 1);
  assert.equal(summary.emptyPtrCount, 0);
  assert.equal(summary.unknownDocTypeCount, 2);
  assert.deepEqual(summary.unknownDocTypeExamples, ['https://example/annual/']);
  assert.deepEqual(summary.outcomes, outcomes);
  assert.equal(summary.listingComplete, false);
  assert.equal(summary.incomplete, true);
});
