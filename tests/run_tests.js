// Acceptance tests for the Node.js Google Maps scrapers.
//
// Two layers, matching the Python suite:
//   1. Pure unit tests for maps_common.js (scroll stall detection, rating /
//      review extraction, fixed-header CSV, zero-result reporting) - no
//      browser needed, always run.
//   2. Browser integration tests against the local fixture page covering
//      normal multi-card, partial missing fields, zero results and an
//      early-bottom feed, for both Selenium and Playwright.
//
// Run with:   node tests/run_tests.js
// Browser integration runs when RUN_BROWSER_TESTS=1 is set.

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert/strict');
const test = require('node:test');

const REPO_ROOT = path.resolve(__dirname, '..');
const NODE_DIR = path.join(REPO_ROOT, 'NodeJS');
const TMP_DIR = path.join(REPO_ROOT, 'tests', 'tmp');
const FIXTURE_URL =
  'file:///' + path.join(REPO_ROOT, 'tests', 'fixtures', 'fixture.html').replace(/\\/g, '/');

const common = require(path.join(NODE_DIR, 'maps_common.js'));
const {
  CSV_HEADER,
  extractRating,
  extractReviews,
  scrollFeed,
  writeCsv,
  report,
} = common;

fs.mkdirSync(TMP_DIR, { recursive: true });

function tmpFile(name) {
  return path.join(TMP_DIR, name);
}

// --------------------------------------------------------------------------
// Unit tests: scroll stall logic
// --------------------------------------------------------------------------

test('scrollFeed stops after two stalled rounds', async () => {
  const heights = [100, 200, 300, 300, 300, 300];
  let index = 0;
  const getHeight = async () => heights[Math.min(index, heights.length - 1)];
  const scrollOnce = async () => {
    index += 1;
  };

  const { scrolls, stopReason } = await scrollFeed(
    scrollOnce,
    getHeight,
    10,
    0
  );
  assert.equal(stopReason, 'reached_bottom');
  assert.equal(scrolls, 4);
});

test('scrollFeed reaches maxScrolls while the panel keeps growing', async () => {
  let height = 100;
  const { scrolls, stopReason } = await scrollFeed(
    async () => {
      height += 100;
    },
    async () => height,
    5,
    0
  );
  assert.equal(scrolls, 5);
  assert.equal(stopReason, 'max_scrolls_reached');
});

test('scrollFeed stops early on an empty feed', async () => {
  const { scrolls, stopReason } = await scrollFeed(
    async () => {},
    async () => 0,
    10,
    0
  );
  assert.equal(scrolls, 2);
  assert.equal(stopReason, 'reached_bottom');
});

test('a single growing round resets the stall counter', async () => {
  const heights = [100, 100, 200, 200, 200];
  let index = 0;
  const { scrolls, stopReason } = await scrollFeed(
    async () => {
      index += 1;
    },
    async () => heights[Math.min(index, heights.length - 1)],
    10,
    0
  );
  assert.equal(scrolls, 4);
  assert.equal(stopReason, 'reached_bottom');
});

// --------------------------------------------------------------------------
// Unit tests: field extraction
// --------------------------------------------------------------------------

test('rating comes from the aria-label number', () => {
  assert.equal(extractRating('Rated 4.6 out of 5 stars'), '4.6');
  assert.equal(extractRating('3.9 stars'), '3.9');
  assert.equal(extractRating(''), '');
  assert.equal(extractRating(null), '');
  assert.equal(extractRating('no digits here'), '');
});

test('review counts drop the thousands separators', () => {
  assert.equal(extractReviews('(1,234)'), '1234');
  assert.equal(extractReviews('12,000 reviews'), '12000');
  assert.equal(extractReviews('56'), '56');
  assert.equal(extractReviews(''), '');
  assert.equal(extractReviews(null), '');
});

// --------------------------------------------------------------------------
// Unit tests: CSV output
// --------------------------------------------------------------------------

test('zero records still writes a header-only CSV', () => {
  const file = tmpFile('node_empty.csv');
  writeCsv(file, []);
  const lines = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '').trim().split(/\r?\n/);
  assert.equal(lines.length, 1);
  assert.equal(lines[0], CSV_HEADER.join(','));
  fs.unlinkSync(file);
});

test('missing fields never remove a column', () => {
  const file = tmpFile('node_missing.csv');
  writeCsv(file, [{ Name: 'Bare', Rating: '' }]);
  const content = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '');
  const lines = content.trim().split(/\r?\n/);
  assert.equal(lines[0], CSV_HEADER.join(','));
  assert.equal(lines[1], 'Bare,,,,,,');
  fs.unlinkSync(file);
});

test('quotes and commas inside values are escaped', () => {
  const file = tmpFile('node_quotes.csv');
  writeCsv(file, [{ Name: 'Joe "The Chef"', Services: 'Dine-in, Delivery' }]);
  const content = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '');
  const lines = content.trim().split(/\r?\n/);
  assert.ok(lines[1].includes('"Joe ""The Chef"""'));
  assert.ok(lines[1].includes('"Dine-in, Delivery"'));
  fs.unlinkSync(file);
});

// --------------------------------------------------------------------------
// Unit tests: console reporting
// --------------------------------------------------------------------------

function captureConsole(fn) {
  const original = console.log;
  const messages = [];
  console.log = (...args) => messages.push(args.join(' '));
  try {
    fn();
  } finally {
    console.log = original;
  }
  return messages.join('\n');
}

test('zero-result report never claims a successful write', () => {
  const output = captureConsole(() => report([], 'out.csv', 2, 'reached_bottom'));
  assert.ok(output.includes('0 records'));
  assert.ok(output.includes('reached bottom'));
  assert.ok(!output.includes('Saved 0'));
});

test('non-zero report shows the count and stop reason', () => {
  const output = captureConsole(() =>
    report([{ Name: 'a' }], 'out.csv', 10, 'max_scrolls_reached')
  );
  assert.ok(output.includes('Saved 1 records'));
  assert.ok(output.includes('max_scrolls'));
});

// --------------------------------------------------------------------------
// Browser integration tests
// --------------------------------------------------------------------------

const RUN_BROWSER = process.env.RUN_BROWSER_TESTS === '1';
const EXPECTED_NORMAL_RECORDS = 3;

function fixtureUrl(scenario) {
  return `${FIXTURE_URL}#${scenario}`;
}

function readCsvRows(file) {
  // Minimal RFC-4180-ish parser sufficient for the fixture output.
  const content = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '').trim();
  const rows = [];
  let row = [];
  let field = '';
  let inQuotes = false;
  for (let i = 0; i < content.length; i += 1) {
    const char = content[i];
    if (inQuotes) {
      if (char === '"') {
        if (content[i + 1] === '"') {
          field += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        field += char;
      }
    } else if (char === '"') {
      inQuotes = true;
    } else if (char === ',') {
      row.push(field);
      field = '';
    } else if (char === '\r') {
      // handled together with \n
    } else if (char === '\n') {
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else {
      field += char;
    }
  }
  row.push(field);
  rows.push(row);
  const header = rows[0];
  return rows.slice(1).map((values) =>
    Object.fromEntries(header.map((key, i) => [key, values[i] ?? '']))
  );
}

async function runScenarios(t, driverName, runScraper) {
  await t.test(`[${driverName}] normal scenario: multiple complete cards`, async () => {
    const file = tmpFile(`node_${driverName}_normal.csv`);
    const { records, scrolls, stopReason } = await runScraper({
      url: fixtureUrl('normal'),
      outputFile: file,
      maxScrolls: 6,
      scrollPause: 200,
      headless: true,
    });
    assert.equal(records.length, EXPECTED_NORMAL_RECORDS);
    const rows = readCsvRows(file);
    assert.equal(rows.length, EXPECTED_NORMAL_RECORDS);
    assert.equal(rows[0].Name, 'Alpha Bistro');
    assert.equal(rows[0].Rating, '4.6');
    assert.equal(rows[0].Reviews, '1234');
    assert.equal(rows[0].Category, 'Restaurant');
    assert.equal(rows[0].Services, 'Dine-in, Delivery');
    assert.ok(rows[0].Image.includes('googleusercontent'));
    assert.ok(rows[0]['Detail URL'].startsWith('https://'));
    assert.equal(stopReason, 'reached_bottom');
    assert.ok(scrolls < 6);
    fs.unlinkSync(file);
  });

  await t.test(`[${driverName}] missing fields degrade to empty strings`, async () => {
    const file = tmpFile(`node_${driverName}_missing.csv`);
    const { records } = await runScraper({
      url: fixtureUrl('missing'),
      outputFile: file,
      maxScrolls: 6,
      scrollPause: 200,
      headless: true,
    });
    assert.equal(records.length, 3);
    const rawHeader = fs
      .readFileSync(file, 'utf8')
      .replace(/^\uFEFF/, '')
      .split(/\r?\n/)[0];
    assert.equal(rawHeader, CSV_HEADER.join(','));
    const rows = readCsvRows(file);
    assert.equal(rows[0].Name, 'No Rating Diner');
    assert.equal(rows[0].Rating, '');
    assert.equal(rows[0].Image, '');
    assert.equal(rows[0].Reviews, '89');
    assert.equal(rows[1].Name, '');
    assert.equal(rows[1]['Detail URL'], '');
    assert.equal(rows[1].Rating, '4.0');
    assert.equal(rows[2].Name, 'Bare Bones');
    for (const column of CSV_HEADER.slice(1)) {
      assert.equal(rows[2][column], '');
    }
    fs.unlinkSync(file);
  });

  await t.test(`[${driverName}] zero results writes header-only CSV`, async () => {
    const file = tmpFile(`node_${driverName}_empty.csv`);
    const { records, scrolls, stopReason } = await runScraper({
      url: fixtureUrl('empty'),
      outputFile: file,
      maxScrolls: 6,
      scrollPause: 200,
      headless: true,
    });
    assert.deepEqual(records, []);
    assert.equal(stopReason, 'reached_bottom');
    assert.equal(scrolls, 2);
    const lines = fs
      .readFileSync(file, 'utf8')
      .replace(/^\uFEFF/, '')
      .trim()
      .split(/\r?\n/);
    assert.equal(lines.length, 1);
    assert.equal(lines[0], CSV_HEADER.join(','));
    fs.unlinkSync(file);
  });

  await t.test(`[${driverName}] early bottom stops well before maxScrolls`, async () => {
    const file = tmpFile(`node_${driverName}_bottom.csv`);
    const { records, scrolls, stopReason } = await runScraper({
      url: fixtureUrl('early-bottom'),
      outputFile: file,
      maxScrolls: 8,
      scrollPause: 200,
      headless: true,
    });
    assert.equal(records.length, EXPECTED_NORMAL_RECORDS);
    assert.equal(stopReason, 'reached_bottom');
    assert.ok(scrolls <= 3, `expected <=3 scrolls, got ${scrolls}`);
    fs.unlinkSync(file);
  });

  await t.test(`[${driverName}] growing feed stops once lazy loading ends`, async () => {
    const file = tmpFile(`node_${driverName}_growing.csv`);
    const { records, scrolls, stopReason } = await runScraper({
      url: fixtureUrl('growing'),
      outputFile: file,
      maxScrolls: 10,
      scrollPause: 200,
      headless: true,
    });
    // 4 batches x 2 cards = 8 records.
    assert.equal(records.length, 8);
    assert.equal(stopReason, 'reached_bottom');
    assert.ok(scrolls < 10, `expected an early stop, got ${scrolls} scrolls`);
    const rows = readCsvRows(file);
    assert.equal(rows[0].Name, 'Growing Place 1');
    assert.equal(rows[rows.length - 1].Name, 'Growing Place 8');
    assert.equal(rows[0].Reviews, '100');
    fs.unlinkSync(file);
  });
}

test('Selenium browser scenarios', { skip: !RUN_BROWSER }, async (t) => {
  let selenium;
  try {
    selenium = require(path.join(NODE_DIR, 'selenium_scraper.js'));
  } catch (error) {
    t.skip(`Selenium unavailable: ${error.message}`);
    return;
  }
  await runScenarios(t, 'selenium', (options) => selenium.runScraper(options));
});

test('Playwright browser scenarios', { skip: !RUN_BROWSER }, async (t) => {
  let playwright;
  try {
    playwright = require(path.join(NODE_DIR, 'playwright_scraper.js'));
  } catch (error) {
    t.skip(`Playwright unavailable: ${error.message}`);
    return;
  }
  await runScenarios(t, 'playwright', (options) =>
    playwright.runScraper({
      ...options,
      channel: process.env.MAPS_BROWSER_CHANNEL || undefined,
    })
  );
});
