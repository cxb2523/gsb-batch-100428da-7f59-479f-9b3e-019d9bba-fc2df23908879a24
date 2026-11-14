// Google Maps listing scraper (Playwright / Node.js).
//
// Mirrors selenium_scraper.js using the shared rules from maps_common.js so
// both drivers handle scroll stalls, missing fields and empty results the
// same way and match the Python implementation.  Defaults target the live
// Google Maps page; runScraper() accepts a local file:// URL for tests.

'use strict';

const fs = require('fs');
const path = require('path');
const {
  SELECTORS,
  extractRating,
  extractReviews,
  normalizeBrowserEnv,
  parseCards,
  report,
  scrollFeed,
  writeCsv,
} = require('./maps_common');

// Stealth is optional: live scraping prefers playwright-extra with the
// stealth plugin, while fixture pages and minimal installs use plain
// playwright.  Both expose the same launch/newContext API surface here.
function loadChromium() {
  try {
    const { chromium } = require('playwright-extra');
    // The maintained stealth plugin is published as
    // puppeteer-extra-plugin-stealth and is also compatible with
    // playwright-extra; the old playwright-extra-plugin-stealth name is a
    // stub package, so try both.
    let StealthPlugin;
    try {
      StealthPlugin = require('puppeteer-extra-plugin-stealth');
    } catch (_) {
      StealthPlugin = require('playwright-extra-plugin-stealth');
    }
    chromium.use(StealthPlugin());
    return chromium;
  } catch (_) {
    return require('playwright').chromium;
  }
}

const DEFAULT_URL = 'https://www.google.com/maps';
const DEFAULT_QUERY = 'restaurants in New York';
const DEFAULT_OUTPUT = 'maps_data_playwright.csv';

// Run a lookup, turning any Playwright error into an empty field.
async function safeCall(action) {
  try {
    return await action();
  } catch (_) {
    return '';
  }
}

// Extract one record.  Every field is fault-tolerant and defaults to ''.
async function parseCard(card) {
  async function firstText(selector) {
    return safeCall(async () => {
      const locator = card.locator(selector);
      return (await locator.count()) > 0 ? locator.nth(0).innerText() : '';
    });
  }

  async function firstAttribute(selector, attribute) {
    return safeCall(async () => {
      const locator = card.locator(selector);
      return (await locator.count()) > 0
        ? locator.nth(0).getAttribute(attribute)
        : '';
    });
  }

  const name = await firstText(SELECTORS.name);

  const ratingAria = await firstAttribute(SELECTORS.rating, 'aria-label');
  const rating = extractRating(ratingAria);

  const reviewsText = await firstText(SELECTORS.reviews);
  const reviews = extractReviews(reviewsText);

  const category = await firstText(SELECTORS.category);

  const services = await safeCall(async () => {
    const locator = card.locator(SELECTORS.services);
    const count = await locator.count();
    const texts = [];
    for (let i = 0; i < count; i += 1) {
      texts.push(await locator.nth(i).innerText());
    }
    return texts.join(', ');
  });

  const imageUrl = await firstAttribute(SELECTORS.image, 'src');
  const detailUrl = await firstAttribute(SELECTORS.detailLink, 'href');

  return {
    Name: name,
    Rating: rating,
    Reviews: reviews,
    Category: category,
    Services: services,
    Image: imageUrl,
    'Detail URL': detailUrl,
  };
}

async function runScraper(options = {}) {
  const {
    url = DEFAULT_URL,
    query = DEFAULT_QUERY,
    outputFile = DEFAULT_OUTPUT,
    maxScrolls = 10,
    scrollPause = 2000,
    headless = false,
    channel,
  } = options;

  normalizeBrowserEnv();
  const chromium = loadChromium();
  const launchOptions = {
    headless,
    args: [
      '--no-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      '--no-first-run',
      '--no-default-browser-check',
    ],
  };
  if (channel) {
    launchOptions.channel = channel;
  }
  const browser = await chromium.launch(launchOptions);
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
  });
  const page = await context.newPage();

  try {
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(scrollPause);

    const searchBox = page.locator('#searchboxinput');
    if ((await searchBox.count()) > 0) {
      await searchBox.fill(query);
      await searchBox.press('Enter');
      await page.waitForTimeout(scrollPause);
    }

    const feed = page.locator(SELECTORS.feed);
    const feedHandle = await feed.elementHandle();

    const { scrolls, stopReason } = await scrollFeed(
      () =>
        page.evaluate(
          (el) => {
            el.scrollTop = el.scrollHeight;
          },
          feedHandle
        ),
      () => page.evaluate((el) => el.scrollHeight, feedHandle),
      maxScrolls,
      scrollPause
    );

    const feedContainer = page.locator(SELECTORS.feedContainer);
    const cardLocator = feedContainer.locator(SELECTORS.card);
    const count = await cardLocator.count();
    const cards = [];
    for (let i = 0; i < count; i += 1) {
      cards.push(cardLocator.nth(i));
    }
    const records = await parseCards(
      () => cards,
      (card) => parseCard(card)
    );

    writeCsv(outputFile, records);
    report(records, outputFile, scrolls, stopReason);
    return { records, scrolls, stopReason };
  } finally {
    await context.close();
    await browser.close();
  }
}

function envBool(name, fallback) {
  const value = process.env[name];
  if (value === undefined) return fallback;
  return ['1', 'true', 'yes'].includes(value.toLowerCase());
}

if (require.main === module) {
  runScraper({
    url: process.env.MAPS_URL || DEFAULT_URL,
    query: process.env.MAPS_QUERY || DEFAULT_QUERY,
    outputFile: process.env.MAPS_OUTPUT || DEFAULT_OUTPUT,
    maxScrolls: Number(process.env.MAPS_MAX_SCROLLS || '10'),
    scrollPause: Number(process.env.MAPS_SCROLL_PAUSE || '2000'),
    headless: envBool('MAPS_HEADLESS', false),
    channel: process.env.MAPS_BROWSER_CHANNEL || undefined,
  }).catch((error) => {
    console.error(error);
    process.exit(1);
  });
}

module.exports = { runScraper, parseCard };
