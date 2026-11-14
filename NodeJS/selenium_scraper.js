// Google Maps listing scraper (Selenium / Node.js).
//
// The scrolling, parsing and CSV rules live in maps_common.js so this file
// only contains Selenium glue.  Defaults target the live Google Maps page;
// runScraper() accepts a local file:// URL so the test suite can exercise
// the same code against fixture pages.

'use strict';

const fs = require('fs');
const path = require('path');
const { Builder, By, Key, until } = require('selenium-webdriver');
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

const DEFAULT_URL = 'https://www.google.com/maps';
const DEFAULT_QUERY = 'restaurants in New York';
const DEFAULT_OUTPUT = 'maps_data.csv';

// Run a lookup, turning any WebDriver error into an empty field.
async function safeCall(action) {
  try {
    return await action();
  } catch (_) {
    return '';
  }
}

// Extract one record.  Every field is fault-tolerant and defaults to ''.
async function parseCard(card) {
  async function firstText(locator) {
    return safeCall(async () => {
      const elements = await card.findElements(locator);
      return elements.length > 0 ? elements[0].getText() : '';
    });
  }

  async function firstAttribute(locator, attribute) {
    return safeCall(async () => {
      const elements = await card.findElements(locator);
      return elements.length > 0 ? elements[0].getAttribute(attribute) : '';
    });
  }

  const name = await firstText(By.css(SELECTORS.name));

  const ratingAria = await firstAttribute(
    By.xpath('.//span[contains(@aria-label, "stars")]'),
    'aria-label'
  );
  const rating = extractRating(ratingAria);

  const reviewsText = await firstText(By.css(SELECTORS.reviews));
  const reviews = extractReviews(reviewsText);

  const category = await firstText(
    By.xpath('.//div[contains(@class, "W4Efsd")]/span[1]')
  );

  const services = await safeCall(async () => {
    const elements = await card.findElements(
      By.xpath('.//div[contains(@class, "ah5Ghc")]/span')
    );
    const texts = [];
    for (const element of elements) {
      texts.push(await element.getText());
    }
    return texts.join(', ');
  });

  const imageUrl = await firstAttribute(
    By.xpath('.//img[contains(@src, "googleusercontent")]'),
    'src'
  );
  const detailUrl = await firstAttribute(By.css(SELECTORS.detailLink), 'href');

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

// Browser/session errors worth a fresh-session retry in flaky environments.
const RETRYABLE_MARKERS = [
  'invalid session id',
  'session not created',
  'unable to receive message from renderer',
  'devtoolsactiveport',
  'web view not found',
  'chrome not reachable',
];

function isRetryable(error) {
  const message = String((error && error.message) || '').toLowerCase();
  return RETRYABLE_MARKERS.some((marker) => message.includes(marker));
}

function buildChromeOptions(chrome, profileDir, headless) {
  const chromeOptions = new chrome.Options();
  if (headless) {
    chromeOptions.addArguments('--headless=new');
  }
  // Stability flags for restricted/containerized environments; harmless on
  // ordinary desktops. Pipe-based DevTools matches Playwright's more robust
  // transport and avoids losing the renderer on short-lived sessions.
  chromeOptions.addArguments(
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--disable-gpu',
    '--disable-software-rasterizer',
    '--disable-extensions',
    '--disable-crash-reporter',
    '--disable-background-networking',
    '--no-first-run',
    '--no-default-browser-check',
    '--remote-debugging-pipe',
    '--log-level=3',
    `--user-data-dir=${profileDir}`,
    '--window-size=1280,900'
  );
  return chromeOptions;
}

// Resolve a chromedriver without Selenium Manager's network lookup. Prefer a
// CHROMEDRIVER_PATH override, then the bundled package; null means discovery.
function resolveDriverPath() {
  const explicitDriver = process.env.CHROMEDRIVER_PATH;
  if (explicitDriver && fs.existsSync(explicitDriver)) {
    return explicitDriver;
  }
  try {
    return require('chromedriver').path;
  } catch (_) {
    return null;
  }
}

async function runScraper(options = {}) {
  const {
    url = DEFAULT_URL,
    query = DEFAULT_QUERY,
    outputFile = DEFAULT_OUTPUT,
    maxScrolls = 10,
    scrollPause = 2000,
    headless = false,
    retries = 3,
  } = options;

  normalizeBrowserEnv();
  const chrome = require('selenium-webdriver/chrome');
  // Dedicated per-session profiles live under a directory we control rather
  // than the redirected SystemTemp/LOCALAPPDATA.
  const profileRoot =
    process.env.MAPS_PROFILE_ROOT ||
    path.join(__dirname, '..', 'tests', 'tmp', 'chrome-profiles');
  fs.mkdirSync(profileRoot, { recursive: true });
  const driverPath = resolveDriverPath();

  let lastError;
  for (let attempt = 1; attempt <= retries; attempt += 1) {
    const profileDir = fs.mkdtempSync(path.join(profileRoot, 'profile-'));
    const chromeOptions = buildChromeOptions(chrome, profileDir, headless);
    const builder = new Builder().forBrowser('chrome').setChromeOptions(chromeOptions);
    if (driverPath) {
      builder.setChromeService(new chrome.ServiceBuilder(driverPath));
    }
    const driver = await builder.build();

    try {
      await driver.get(url);
      await driver.sleep(scrollPause);

      // The search box only exists on the live page; fixture pages skip it.
      const searchBoxes = await driver.findElements(By.id('searchboxinput'));
      if (searchBoxes.length > 0) {
        await searchBoxes[0].sendKeys(query, Key.RETURN);
        await driver.sleep(scrollPause);
      }

      const feed = await driver.wait(
        until.elementLocated(By.css(SELECTORS.feed)),
        10000
      );

      const { scrolls, stopReason } = await scrollFeed(
        () =>
          driver.executeScript(
            'arguments[0].scrollTop = arguments[0].scrollHeight',
            feed
          ),
        () =>
          driver.executeScript('return arguments[0].scrollHeight', feed),
        maxScrolls,
        scrollPause
      );

      const feedContainer = await driver.findElement(
        By.css(SELECTORS.feedContainer)
      );
      const cards = await feedContainer.findElements(By.css(SELECTORS.card));
      const records = await parseCards(
        () => cards,
        (card) => parseCard(card)
      );

      writeCsv(outputFile, records);
      report(records, outputFile, scrolls, stopReason);
      return { records, scrolls, stopReason };
    } catch (error) {
      lastError = error;
      if (attempt < retries && isRetryable(error)) {
        console.log(
          `Browser session failed (${error.name}); retrying ${attempt}/${retries - 1}...`
        );
        continue;
      }
      throw error;
    } finally {
      try {
        await driver.quit();
      } catch (_) {
        /* best effort */
      }
      fs.rmSync(profileDir, { recursive: true, force: true });
    }
  }

  throw lastError;
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
  }).catch((error) => {
    console.error(error);
    process.exit(1);
  });
}

module.exports = { runScraper, parseCard };
