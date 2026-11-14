/**
 * Full flow example: stealth + a random user agent + proxy failover.
 *
 * The flow is:
 *
 *   pick a random proxy order -> for each proxy:
 *       open a fresh browser context with that proxy and a random UA
 *       navigate to the target
 *       on proxy/network/HTTP failure: close the context, record the failure,
 *       continue with the next proxy
 *   -> on success return the HTML, the proxy/UA actually used and the failures
 *   -> when every proxy failed throw `ProxyRotationError`
 *
 * `scrapeWithProxyRotation` is the public entry point and mirrors the Python
 * implementation in `Python/full_flow_example.py` (same function name in the
 * usual naming style, same result field names).
 *
 * Configuration precedence:
 *
 *   explicit function argument > environment variable > constant below
 *
 * Environment variables
 * ---------------------
 * FULL_FLOW_PROXIES       Proxy pool, newline- or comma-separated (JSON array
 *                         also accepted). Example:
 *                         FULL_FLOW_PROXIES="http://127.0.0.1:1
 *                         http://127.0.0.1:2"
 * FULL_FLOW_USER_AGENTS   UA pool, newline-separated (JSON array also accepted;
 *                         do not use commas, UA strings contain commas).
 * FULL_FLOW_TARGET_URL    Target URL (default `https://example.com`).
 * FULL_FLOW_PROXY_BYPASS  Optional proxy bypass list, e.g. `<-loopback>`.
 * FULL_FLOW_HEADLESS      `1`/`0` for the command line entry point.
 * FULL_FLOW_TIMEOUT_MS    Navigation timeout in milliseconds (default 60000).
 * FULL_FLOW_PRINT_JSON    `1` prints the full result as JSON instead of the
 *                         first 500 characters of the HTML.
 *
 * Running this script directly performs one failover run, prints the first 500
 * characters of the page (or the result JSON) and exits non-zero when every
 * proxy failed (1) or the configuration is invalid (2).
 *
 * Note: running this against local mock proxies only proves the rotation and
 * error-handling logic.  It is not evidence that a real proxy + real target
 * combination works; validate that separately.
 */

let chromium;
let stealthEnabled = false;

// playwright-extra + its stealth plugin are the documented setup.  Some
// published versions of `playwright-extra-plugin-stealth` are only a
// placeholder that throws on require, and `playwright` alone still works, so
// fall back to plain Playwright when the plugin cannot be loaded.
try {
  ({ chromium } = require('playwright-extra'));
  const stealth = require('playwright-extra-plugin-stealth');
  chromium.use(stealth());
  stealthEnabled = true;
} catch (stealthError) {
  ({ chromium } = require('playwright'));
}

// Proxy pool and UA pool are still plain in-script constants.  The placeholders
// below are NOT reachable; inject a real/working pool through the function
// arguments or the FULL_FLOW_* environment variables.
const PROXIES = [
  'http://username:password@proxy1.example.com:8000',
  'http://username:password@proxy2.example.com:8000',
];

const USER_AGENTS = [
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.1 Safari/605.1.15',
];

const DEFAULT_TARGET_URL = 'https://example.com';
const DEFAULT_TIMEOUT_MS = 60000;
const MAX_ERROR_CHARS = 500;

const ENV_PROXIES = 'FULL_FLOW_PROXIES';
const ENV_USER_AGENTS = 'FULL_FLOW_USER_AGENTS';
const ENV_TARGET_URL = 'FULL_FLOW_TARGET_URL';
const ENV_PROXY_BYPASS = 'FULL_FLOW_PROXY_BYPASS';
const ENV_HEADLESS = 'FULL_FLOW_HEADLESS';
const ENV_TIMEOUT_MS = 'FULL_FLOW_TIMEOUT_MS';
const ENV_PRINT_JSON = 'FULL_FLOW_PRINT_JSON';

/** Raised when every proxy in the pool failed. */
class ProxyRotationError extends Error {
  constructor(targetUrl, failedAttempts) {
    const attempts = [...failedAttempts];
    let message = `All ${attempts.length} proxy attempt(s) failed for ${targetUrl}.`;
    if (attempts.length > 0) {
      const details = attempts
        .map(
          (attempt, index) =>
            `[${index + 1}] ${attempt.proxy} -> ${attempt.error_type}: ${attempt.error}`,
        )
        .join('; ');
      message = `${message} Attempts: ${details}`;
    }
    super(message);
    this.name = 'ProxyRotationError';
    this.target_url = targetUrl;
    this.failed_attempts = attempts;
    this.attempted_count = attempts.length;
  }
}

/** The proxy answered, but the navigation ended with an HTTP error. */
class HttpStatusError extends Error {
  constructor(status, targetUrl) {
    super(`HTTP ${status} for ${targetUrl}`);
    this.name = 'HttpStatusError';
    this.status = status;
  }
}

/** Navigation "succeeded" but produced no HTML; treat it as a failure. */
class EmptyContentError extends Error {
  constructor(message) {
    super(message);
    this.name = 'EmptyContentError';
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function humanLikeInteraction(page) {
  await page.mouse.move(100, 100);
  await sleep(Math.random() * 1000 + 500);
  await page.mouse.move(200, 300);
  await sleep(Math.random() * 1000 + 500);
  await page.evaluate(() => window.scrollBy(0, window.innerHeight / 2));
  await sleep(Math.random() * 1000 + 1000);
}

function parsePoolEnv(value, { allowCommas }) {
  if (value === undefined || value === null) {
    return null;
  }
  const text = String(value).trim();
  if (text === '') {
    return null;
  }
  if (text.startsWith('[')) {
    const parsed = JSON.parse(text);
    if (!Array.isArray(parsed) || !parsed.every((item) => typeof item === 'string')) {
      throw new Error('pool environment variable must be a JSON array of strings');
    }
    return parsed;
  }
  const separator = allowCommas ? /[\r\n,]+/ : /[\r\n]+/;
  return text.split(separator).map((item) => item.trim()).filter(Boolean);
}

function cleanPool(items, name) {  const cleaned = [];
  const seen = new Set();
  for (const item of items) {
    const value = String(item).trim();
    if (value === '' || seen.has(value)) {
      continue;
    }
    seen.add(value);
    cleaned.push(value);
  }
  if (cleaned.length === 0) {
    throw new Error(`${name} must contain at least one non-empty entry`);
  }
  return cleaned;
}

/**
 * Resolve a pool from an argument, an environment variable or a constant.
 *
 * A bare string is split the same way the environment variable would be, so
 * `proxies: 'a,b'` behaves like `FULL_FLOW_PROXIES=a,b`.
 */
function resolvePool(value, envName, fallback, { allowCommas }) {
  if (value !== undefined && value !== null) {
    if (typeof value === 'string') {
      return parsePoolEnv(value, { allowCommas }) || fallback;
    }
    return value;
  }
  return parsePoolEnv(process.env[envName], { allowCommas }) || fallback;
}

/**
 * Split a proxy URL into Playwright settings plus a masked identifier.
 *
 * Playwright takes `server`/`username`/`password` separately, so the
 * credential part of `http://user:pass@host:port` is moved into its own
 * fields here.  The returned identifier masks the password but keeps the
 * username and host:port; together with `proxy_index` it remains possible to
 * match a record back to the exact pool entry.
 */
function proxyConfig(proxy) {
  let raw = String(proxy).trim();
  if (!raw) {
    throw new Error('empty proxy URL');
  }
  if (!raw.includes('://')) {
    raw = `http://${raw}`;
  }

  let parsed;
  try {
    parsed = new URL(raw);
  } catch (error) {
    throw new Error(`invalid proxy URL: ${proxy}`);
  }
  if (!parsed.hostname) {
    throw new Error(`invalid proxy URL: ${proxy}`);
  }

  const scheme = parsed.protocol.replace(/:$/, '') || 'http';
  let host = parsed.hostname;
  if (host.includes(':') && !host.startsWith('[')) {
    host = `[${host}]`;
  }
  const portPart = parsed.port ? `:${parsed.port}` : '';
  const server = `${scheme}://${host}${portPart}`;

  const settings = { server };
  let masked = server;
  const hasCredentials = parsed.username !== '' || parsed.password !== '';
  if (hasCredentials) {
    let username = parsed.username;
    let password = parsed.password;
    try {
      username = decodeURIComponent(username);
      password = decodeURIComponent(password);
    } catch (error) {
      // Keep the raw percent-encoded values if they are not valid escapes.
    }
    settings.username = username;
    if (parsed.password !== '') {
      settings.password = password;
    }
    const credentials = parsed.password === '' ? username : `${username}:***`;
    masked = `${scheme}://${credentials}@${host}${portPart}`;
  }

  return { settings, masked };
}

/** Best-effort masking for a proxy URL that could not be parsed. */
function fallbackMask(proxy) {
  const raw = String(proxy).trim();
  const at = raw.lastIndexOf('@');
  if (at !== -1) {
    const prefix = raw.slice(0, at);
    const suffix = raw.slice(at + 1);
    const scheme = prefix.includes('://') ? `${prefix.split('://')[0]}://` : '';
    return `${scheme}***@${suffix}`;
  }
  return raw.slice(0, 200);
}

function errorText(error) {
  const text = String((error && error.message) || error || '')
    .trim()
    .replace(/[\r\n]+/g, ' ');
  return text.slice(0, MAX_ERROR_CHARS);
}

function errorType(error) {
  return (error && error.name) || 'Error';
}

function booleanEnv(name, defaultValue) {
  const value = process.env[name];
  if (value === undefined) {
    return defaultValue;
  }
  return !['0', 'false', 'no', 'off', ''].includes(value.trim().toLowerCase());
}

function intEnv(name, defaultValue) {
  const value = process.env[name];
  if (value === undefined || value.trim() === '') {
    return defaultValue;
  }
  const parsed = Number.parseInt(value, 10);
  if (Number.isNaN(parsed)) {
    throw new Error(`${name} must be an integer number of milliseconds, got ${JSON.stringify(value)}`);
  }
  return parsed;
}

/**
 * Load one page, rotating proxies on failure.
 *
 * Options:
 *   proxies       Proxy pool. Defaults to FULL_FLOW_PROXIES, then PROXIES.
 *                 Each proxy is tried at most once, in random order;
 *                 duplicates are removed.
 *   userAgents    UA pool. Defaults to FULL_FLOW_USER_AGENTS, then
 *                 USER_AGENTS. One random UA is picked per attempt.
 *   targetUrl     URL to load. Defaults to FULL_FLOW_TARGET_URL, then
 *                 https://example.com.
 *   headless      Run Chromium headless (default true).
 *   timeoutMs     Navigation timeout in milliseconds (default 60000).
 *   proxyBypass   Optional Playwright proxy bypass list (e.g. `<-loopback>`).
 *
 * Resolves to `{success, url, html, proxy, proxy_index, user_agent,
 * failed_attempts, attempt_count}` — the same field names as Python.
 *
 * Rejects with `ProxyRotationError` when every proxy failed; its
 * `attempted_count` and `failed_attempts` say how many proxies were tried and
 * why each failed.
 */
async function scrapeWithProxyRotation(options = {}) {
  const proxyPoolSource = resolvePool(options.proxies, ENV_PROXIES, PROXIES, {
    allowCommas: true,
  });
  const uaPoolSource = resolvePool(options.userAgents, ENV_USER_AGENTS, USER_AGENTS, {
    allowCommas: false,
  });

  const proxyPool = cleanPool(proxyPoolSource, 'proxy pool');
  const uaPool = cleanPool(uaPoolSource, 'user agent pool');

  const url = options.targetUrl || process.env[ENV_TARGET_URL] || DEFAULT_TARGET_URL;
  const timeoutMs = options.timeoutMs ?? intEnv(ENV_TIMEOUT_MS, DEFAULT_TIMEOUT_MS);
  const headless =
    options.headless !== undefined ? options.headless : booleanEnv(ENV_HEADLESS, true);
  const proxyBypass =
    options.proxyBypass !== undefined ? options.proxyBypass : process.env[ENV_PROXY_BYPASS] || null;

  // Keep the original pool position for reporting, then shuffle so every
  // proxy is attempted exactly once in random order.
  const indexedPool = proxyPool.map((proxy, index) => ({ proxy, index }));
  for (let i = indexedPool.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [indexedPool[i], indexedPool[j]] = [indexedPool[j], indexedPool[i]];
  }

  const failedAttempts = [];
  let browser;
  try {
    browser = await chromium.launch({ headless });
    for (let position = 0; position < indexedPool.length; position += 1) {
      const { proxy, index: proxyIndex } = indexedPool[position];
      let maskedProxy = fallbackMask(proxy);
      const userAgent = uaPool[Math.floor(Math.random() * uaPool.length)];
      let context;
      try {
        const { settings, masked } = proxyConfig(proxy);
        maskedProxy = masked;
        if (proxyBypass) {
          settings.bypass = proxyBypass;
        }

        context = await browser.newContext({
          userAgent,
          proxy: settings,
        });
        // playwright-extra applies stealth patches inside newContext/newPage
        // through the plugin; plain Playwright needs nothing extra here.
        const page = await context.newPage();

        const response = await page.goto(url, {
          timeout: timeoutMs,
          waitUntil: 'load',
        });
        const status = response ? response.status() : null;
        if (status === null) {
          throw new Error(`navigation returned no response for ${url}`);
        }
        if (status >= 400) {
          throw new HttpStatusError(status, url);
        }

        await humanLikeInteraction(page);
        const html = await page.content();
        if (!html || !html.trim()) {
          throw new EmptyContentError(
            `page returned empty HTML for ${url} (proxy ${maskedProxy})`,
          );
        }

        return {
          success: true,
          url,
          html,
          proxy: maskedProxy,
          proxy_index: proxyIndex,
          user_agent: userAgent,
          failed_attempts: failedAttempts,
          attempt_count: position + 1,
        };
      } catch (error) {
        failedAttempts.push({
          proxy: maskedProxy,
          proxy_index: proxyIndex,
          user_agent: userAgent,
          error_type: errorType(error),
          error: errorText(error),
        });
      } finally {
        if (context) {
          try {
            await context.close();
          } catch (closeError) {
            // The context may already be gone after a crash; never hide the
            // navigation error we are reporting.
          }
        }
      }
    }
  } finally {
    if (browser) {
      try {
        await browser.close();
      } catch (closeError) {
        // Never let a close failure mask ProxyRotationError, which carries the
        // attempt count the caller needs.
      }
    }
  }

  throw new ProxyRotationError(url, failedAttempts);
}

/** Command line entry point: one failover run and a short report. */
async function main() {
  const result = await scrapeWithProxyRotation();

  if ((process.env[ENV_PRINT_JSON] || '').trim() === '1') {
    console.log(JSON.stringify(result, null, 2));
  } else {
    console.log(result.html.slice(0, 500)); // print first 500 chars
  }

  console.error(
    `[full-flow] success: proxy=${result.proxy} (index ${result.proxy_index}), ` +
      `user_agent=${JSON.stringify(result.user_agent)}, ` +
      `failed_attempts=${result.failed_attempts.length}, ` +
      `attempt_count=${result.attempt_count}`,
  );
  return result;
}

if (require.main === module) {
  main().catch((error) => {
    if (error instanceof ProxyRotationError) {
      console.error(`[full-flow] ERROR: ${error.message}`);
      error.failed_attempts.forEach((attempt, index) => {
        console.error(
          `[full-flow]   attempt ${index + 1}: proxy=${attempt.proxy} ` +
            `(index ${attempt.proxy_index}) -> ` +
            `${attempt.error_type}: ${attempt.error}`,
        );
      });
      process.exitCode = 1;
      return;
    }
    const message = (error && error.message) || String(error);
    const isConfigError =
      /must contain at least one|must be a JSON array|must be an integer/.test(message) ||
      /invalid proxy URL|empty proxy URL/i.test(message);
    if (isConfigError) {
      console.error(`[full-flow] CONFIG ERROR: ${message}`);
      process.exitCode = 2;
      return;
    }
    console.error(`[full-flow] ERROR: ${error && error.stack ? error.stack : error}`);
    process.exitCode = 1;
  });
}

module.exports = {
  ProxyRotationError,
  HttpStatusError,
  EmptyContentError,
  humanLikeInteraction,
  scrapeWithProxyRotation,
  // Snake-case alias so the public name matches Python exactly.
  scrape_with_proxy_rotation: scrapeWithProxyRotation,
  stealthEnabled,
  PROXIES,
  USER_AGENTS,
  DEFAULT_TARGET_URL,
  DEFAULT_TIMEOUT_MS,
  __test__: { proxyConfig, fallbackMask, parsePoolEnv, cleanPool },
};
