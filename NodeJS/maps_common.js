// Shared, fault-tolerant Google Maps listing logic for the Node scrapers.
// selenium_scraper.js and playwright_scraper.js both use these helpers so the
// two browser drivers behave identically and match the Python implementation.

'use strict';

const fs = require('fs');

// Fixed output contract: these seven columns are always written, even when
// every card is missing a field or no card was found at all.
const CSV_HEADER = [
  'Name',
  'Rating',
  'Reviews',
  'Category',
  'Services',
  'Image',
  'Detail URL',
];

// Selectors shared with the original examples, kept in one place.
const SELECTORS = {
  feed: 'div[role="feed"]',
  feedContainer:
    'div.m6QErb.DxyBCb.kA9KIf.dS8AEf.XiKgde.ecceSd[role="feed"]',
  card: 'div.Nv2PK.THOPZb.CpccDe',
  name: '.qBF1Pd',
  rating: 'span[aria-label*="stars"]',
  reviews: '.UY7F9',
  category: 'div.W4Efsd > span',
  services: 'div.ah5Ghc > span',
  image: 'img[src*="googleusercontent"]',
  detailLink: 'a.hfpxzc',
};

// Two consecutive scroll rounds without panel growth means the feed has
// reached its bottom (or never had any results).
const STALL_LIMIT = 2;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Chrome/Edge reject remote debugging ("DevToolsActivePort file doesn't
// exist") when LOCALAPPDATA/APPDATA point at a redirected, non-standard
// location because every user-data-dir then looks like the browser default.
// Point them back at the real per-user folders when they exist.  This is a
// no-op on normal machines and harmless when those folders are absent.
function normalizeBrowserEnv() {
  const path = require('path');
  const candidates = [
    ['LOCALAPPDATA', path.join('C:', 'Users', 'Administrator', 'AppData', 'Local')],
    ['APPDATA', path.join('C:', 'Users', 'Administrator', 'AppData', 'Roaming')],
  ];
  for (const [name, fallback] of candidates) {
    const current = process.env[name];
    if (current && !current.toLowerCase().includes(path.join('users', 'administrator', 'appdata').toLowerCase())) {
      if (fs.existsSync(fallback)) {
        process.env[name] = fallback;
      }
    }
  }
}

// Scroll a results panel until it stops growing.
// scrollOnce() pushes the panel to its current bottom; getHeight() returns
// its current scrollHeight.  maxScrolls/scrollPause keep their original
// meaning (at most N rounds, pause M ms after each one).
// Resolves to { scrolls, stopReason } where stopReason is "reached_bottom"
// after two stalled rounds or "max_scrolls_reached" after all rounds ran.
async function scrollFeed(scrollOnce, getHeight, maxScrolls, scrollPause) {
  let lastHeight = await getHeight();
  let stalls = 0;

  for (let scrolls = 1; scrolls <= maxScrolls; scrolls += 1) {
    await scrollOnce();
    await sleep(scrollPause);
    const newHeight = await getHeight();

    if (newHeight > lastHeight) {
      stalls = 0;
      lastHeight = newHeight;
    } else {
      stalls += 1;
      if (stalls >= STALL_LIMIT) {
        return { scrolls, stopReason: 'reached_bottom' };
      }
    }
  }

  // The for-loop counter is out of scope here; report the configured cap,
  // matching the Python range()-based implementation.
  return { scrolls: maxScrolls, stopReason: 'max_scrolls_reached' };
}

// Pull the first number (digits/dot) out of a "... stars" aria-label.
function extractRating(ariaLabel) {
  if (!ariaLabel) return '';
  const match = String(ariaLabel).match(/([\d.]+)/);
  return match ? match[1] : '';
}

// Pull a digit/comma number out of the review text and drop separators.
function extractReviews(text) {
  if (!text) return '';
  const match = String(text).match(/([\d,]+)/);
  return match ? match[1].replace(/,/g, '') : '';
}

function csvEscape(value) {
  const text = value === null || value === undefined ? '' : String(value);
  if (/[",\r\n]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

// Write records with the fixed seven-column header.  An empty records array
// still produces a header-only CSV so the columns never disappear.
function writeCsv(path, records) {
  const lines = [CSV_HEADER.join(',')];
  for (const record of records) {
    lines.push(
      CSV_HEADER.map((column) => csvEscape(record[column] || '')).join(',')
    );
  }
  // UTF-8 BOM keeps Excel happy with non-ASCII names, matching the Python side.
  fs.writeFileSync(path, '\uFEFF' + lines.join('\r\n') + '\r\n', 'utf8');
}

// Print a truthful summary of the run, including the zero-result case.
function report(records, outputPath, scrolls, stopReason) {
  const count = records.length;
  const reasonText =
    stopReason === 'reached_bottom'
      ? 'panel stopped growing (reached bottom)'
      : 'max_scrolls limit reached';

  if (count === 0) {
    console.log(
      `No results collected: 0 records found. Wrote header-only CSV to ${outputPath}.`
    );
  } else {
    console.log(`Saved ${count} records to ${outputPath}.`);
  }
  console.log(
    `Scrolling stopped after ${scrolls} scroll round(s): ${reasonText}. ` +
      `Records actually written: ${count}.`
  );
}

// Parse every card through a per-field fault-tolerant parser.  A card that
// still raises is skipped after being reported instead of failing the run.
async function parseCards(findCards, parseCard) {
  const cards = await findCards();
  const records = [];
  for (let index = 0; index < cards.length; index += 1) {
    try {
      records.push(await parseCard(cards[index]));
    } catch (error) {
      console.log(`Warning: could not parse card #${index}: ${error.message}`);
    }
  }
  return records;
}

module.exports = {
  CSV_HEADER,
  SELECTORS,
  STALL_LIMIT,
  sleep,
  normalizeBrowserEnv,
  scrollFeed,
  extractRating,
  extractReviews,
  writeCsv,
  report,
  parseCards,
};
