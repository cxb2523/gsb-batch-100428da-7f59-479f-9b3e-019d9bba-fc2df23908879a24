const { test } = require('node:test');
const assert = require('node:assert/strict');
const { normalize, normalizeAll } = require('../dist/transformer/normalize.js');

// Real row captured from Senate EFD PTR b999bc0e-3eb0-4ca9-ab07-8e8f2e04b41f
// (Alan Armstrong, AvalonBay -> Vivmark asset exchange). Before the fix,
// raw type "Exchange" had no TYPE_MAP entry, normalizeType() returned null,
// and the row was silently dropped (reason=unrecognized_type).
function exchangeRow(overrides = {}) {
  return {
    politician: 'Alan Armstrong',
    transaction_date: '08/14/2026',
    filing_date: '09/17/2026',
    ticker: '--',
    asset_name:
      'AvalonBay Communities, Inc. Common Stock (AVB) (Exchanged) VMRK - Vivmark Residential Common Shares of Beneficial Interest (Received)',
    asset_type: 'Stock',
    type: 'Exchange',
    amount: '$1,001 - $15,000',
    owner: 'Joint',
    source_id: 'b999bc0e-3eb0-4ca9-ab07-8e8f2e04b41f|2',
    filing_type: 'original',
    amendment_number: null,
    raw_json: {},
    ...overrides,
  };
}

test('Senate "Exchange" rows normalize to type "exchange", not dropped', () => {
  const result = normalize(exchangeRow());
  assert.notEqual(result, null, 'exchange row must not be skipped as unrecognized_type');
  assert.equal(result.type, 'exchange');
  assert.equal(result.politician, 'Alan Armstrong');
  assert.equal(result.amount_min, 1001);
  assert.equal(result.amount_max, 15000);
  assert.equal(result.owner, 'joint');
  assert.equal(result.parse_status, 'ok');
  assert.equal(result.pdf_url, null);
});

test('normalizeAll keeps an exchange row alongside buy/sell rows', () => {
  const buyRow = exchangeRow({
    type: 'Purchase',
    source_id: 'b999bc0e-3eb0-4ca9-ab07-8e8f2e04b41f|0',
  });
  const results = normalizeAll([buyRow, exchangeRow()]);
  assert.equal(results.length, 2, 'exchange row must survive normalizeAll, not be skipped');
  assert.deepEqual(
    results.map((r) => r.type).sort(),
    ['buy', 'exchange'],
  );
});

test('an actually-unrecognized type is still skipped (regression guard)', () => {
  const result = normalize(exchangeRow({ type: 'Something Else Entirely' }));
  assert.equal(result, null);
});

// ─── Ticker fallback extraction from asset_name ───────────────────────────────

function buyRow(overrides = {}) {
  return {
    politician: 'Test Senator',
    transaction_date: '08/14/2026',
    filing_date: '09/17/2026',
    ticker: '',
    asset_name: 'placeholder',
    asset_type: 'Stock',
    type: 'Purchase',
    amount: '$1,001 - $15,000',
    owner: 'Self',
    source_id: 'test-source-id',
    filing_type: 'original',
    amendment_number: null,
    raw_json: {},
    ...overrides,
  };
}

test('extracts ticker from trailing "(XXXX)" pattern', () => {
  const result = normalize(buyRow({ asset_name: 'Electronic Arts Inc. (EA)' }));
  assert.equal(result.ticker, 'EA');
});

test('extracts ticker from leading "XXXX - Company" pattern', () => {
  const result = normalize(buyRow({ asset_name: 'EA - Electronic Arts Inc' }));
  assert.equal(result.ticker, 'EA');
});

test('exchange row: takes the first ticker (given asset), not the received one', () => {
  const result = normalize(exchangeRow());
  assert.equal(
    result.ticker,
    'AVB',
    'should extract AVB (the asset being reported), not VMRK (the received asset)',
  );
});

test('does not extract a ticker from an "(LLC)"-style entity suffix', () => {
  const result = normalize(
    buyRow({ asset_name: 'Not Fade Away LLC Private Investment Fund (New York, NY)' }),
  );
  assert.equal(result.ticker, null);
});

// State postal codes are NOT stoplisted — they collide with real tickers.
test('extracts "(MA)" as Mastercard\'s ticker, not a Massachusetts state code', () => {
  const result = normalize(buyRow({ asset_name: 'Mastercard Incorporated Common Stock (MA)' }));
  assert.equal(result.ticker, 'MA');
});

test('extracts "(MS)" as Morgan Stanley\'s ticker, not a Mississippi state code', () => {
  const result = normalize(buyRow({ asset_name: 'Morgan Stanley (MS)' }));
  assert.equal(result.ticker, 'MS');
});

test('extracts "(DE)" as Deere\'s ticker, not a Delaware state code', () => {
  const result = normalize(buyRow({ asset_name: 'Deere & Company (DE)' }));
  assert.equal(result.ticker, 'DE');
});

test('does not extract a ticker from a "(City, ST)" address suffix', () => {
  const result = normalize(buyRow({ asset_name: 'webAI, Inc. Common Stock (Austin, TX)' }));
  assert.equal(result.ticker, null);
});

test('does not extract a ticker from stoplisted qualifiers like "(THE)"', () => {
  const result = normalize(buyRow({ asset_name: 'Some Trust Fund (THE)' }));
  assert.equal(result.ticker, null);
});

test('structured ticker field still takes priority over asset_name fallback', () => {
  const result = normalize(
    buyRow({ ticker: 'MSFT', asset_name: 'Microsoft Corp (Different Corp Inc)' }),
  );
  assert.equal(result.ticker, 'MSFT');
});

// ─── asset_subtype derivation (asset_type === "Stock" only) ────────────────────

test('asset_subtype "ETF" for an ETF filed as asset_type "Stock"', () => {
  const result = normalize(
    buyRow({ asset_type: 'Stock', asset_name: 'iShares Core S&P 500 ETF' }),
  );
  assert.equal(result.asset_subtype, 'ETF');
});

test('asset_subtype "Mutual Fund" for a fund filed as asset_type "Stock"', () => {
  const result = normalize(
    buyRow({
      asset_type: 'Stock',
      asset_name: 'Westwood Quality SmallCap Fund - Institutional Ultra Shares',
    }),
  );
  assert.equal(result.asset_subtype, 'Mutual Fund');
});

test('asset_subtype "ETF" for a leveraged/2X ETF filed as asset_type "Stock"', () => {
  const result = normalize(
    buyRow({ asset_type: 'Stock', asset_name: 'Tradr 2X Long SPY Monthly ETF' }),
  );
  assert.equal(result.asset_subtype, 'ETF');
});

test('asset_subtype stays null when asset_type is not "Stock", even with "Fund" in the name', () => {
  // Blumenthal-style row: source labels this "Other", not "Stock" — the
  // fallback must not override a more-specific non-Stock source label.
  const result = normalize(
    buyRow({
      asset_type: 'Other',
      asset_name: 'Not Fade Away LLC Private Investment Fund Description: Hedge Fund',
    }),
  );
  assert.equal(result.asset_subtype, null);
});

test('asset_subtype is null for an ordinary stock with no ETF/Fund wording', () => {
  const result = normalize(
    buyRow({ asset_type: 'Stock', asset_name: 'Wells Fargo & Company Common Stock' }),
  );
  assert.equal(result.asset_subtype, null);
});

// ─── Amount parsing: single exact amount with cents ────────────────────────────

test('a single exact-dollar amount with cents keeps its cents, not truncated to a whole dollar', () => {
  // Regression guard: stripAmount() used to run parseInt() on the cleaned
  // amount string, which silently truncates "2,722.50" to 2722 (parseInt
  // stops at the first non-digit character, the decimal point). Confirmed
  // real-world case: House DocID 20034999.
  const result = normalize(buyRow({ amount: '$2,722.50' }));
  assert.equal(result.amount_min, 2722.5);
  assert.equal(result.amount_max, 2722.5);
});

test('a whole-dollar range still parses as plain integers (no unintended float drift)', () => {
  const result = normalize(buyRow({ amount: '$1,001 - $15,000' }));
  assert.equal(result.amount_min, 1001);
  assert.equal(result.amount_max, 15000);
});
