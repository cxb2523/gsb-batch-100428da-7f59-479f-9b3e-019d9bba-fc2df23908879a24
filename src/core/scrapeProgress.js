/**
 * Batch scrape progress store.
 *
 * A batch that merges several sites can fail per site and per movie: one site
 * may be rate limited (or hit its session limit) while the others still return
 * data. The resulting data/scrape/{id}.json alone cannot tell a finished movie
 * from one that is missing a site, so resume needs its own record:
 *
 *   data/scrape/.scrape-progress.json
 *
 * The file is dot-prefixed so the scrape readers (which list *.json) ignore
 * it. Shape:
 *
 *   {
 *     "version": 1,
 *     "updatedAt": "ISO",
 *     "scrapers": ["javlibrary", "r18dev"],
 *     "lastRun": { "startedAt", "finishedAt", "codes", "skipped", "summary" },
 *     "items": {
 *       "ABP-420": {
 *         "status": "success" | "partial" | "failed",
 *         "updatedAt": "ISO",
 *         "sources": ["javlibrary"],
 *         "scrapers": {
 *           "javlibrary": { "status": "ok" },
 *           "r18dev": { "status": "error", "errorKind": "rate_limited", "reason": "..." }
 *         },
 *         "failedSites": [{ "scraper", "reason", "errorKind" }],
 *         "notFoundSites": [{ "scraper", "reason" }]
 *       }
 *     }
 *   }
 *
 * Per-site status distinguishes a site error ("error": crash, rate limit,
 * session limit, bad output) from the site genuinely not having the movie
 * ("not_found"). Per-movie status:
 *   success = merged data saved, no site errored
 *   partial = merged data saved, at least one site errored
 *   failed  = nothing could be merged (all sites errored or had no data)
 */

const fs = require('fs');
const path = require('path');
const { getScrapePath, getScrapeProgressPath } = require('./config');

const PROGRESS_VERSION = 1;

const STATUS_SUCCESS = 'success';
const STATUS_PARTIAL = 'partial';
const STATUS_FAILED = 'failed';

const SITE_OK = 'ok';
const SITE_NOT_FOUND = 'not_found';
const SITE_ERROR = 'error';

function emptyProgress() {
  return {
    version: PROGRESS_VERSION,
    updatedAt: '',
    scrapers: [],
    items: {}
  };
}

/**
 * Read the progress file, tolerating a missing or corrupted one.
 *
 * @param {string} [scrapeDir] - scrape directory (defaults to data/scrape)
 * @returns {object} progress object (never null)
 */
function readScrapeProgress(scrapeDir = getScrapePath()) {
  const progressPath = path.join(scrapeDir, path.basename(getScrapeProgressPath()));

  let raw;
  try {
    raw = fs.readFileSync(progressPath, 'utf8');
  } catch (error) {
    if (error.code !== 'ENOENT') {
      console.error(`[ScrapeProgress] Cannot read ${progressPath}: ${error.message}`);
    }
    return emptyProgress();
  }

  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return emptyProgress();
    if (!parsed.items || typeof parsed.items !== 'object' || Array.isArray(parsed.items)) {
      parsed.items = {};
    }
    if (!Array.isArray(parsed.scrapers)) parsed.scrapers = [];
    return parsed;
  } catch (error) {
    console.error(`[ScrapeProgress] Ignoring corrupted ${progressPath}: ${error.message}`);
    return emptyProgress();
  }
}

/**
 * Write the progress file atomically (temp file + rename) so an interrupted
 * batch cannot leave a half-written resume file behind.
 *
 * @param {object} progress - progress object
 * @param {string} [scrapeDir] - scrape directory (defaults to data/scrape)
 * @returns {string} path written
 */
function writeScrapeProgress(progress, scrapeDir = getScrapePath()) {
  const progressPath = path.join(scrapeDir, path.basename(getScrapeProgressPath()));
  fs.mkdirSync(scrapeDir, { recursive: true });

  const payload = {
    ...progress,
    version: progress.version || PROGRESS_VERSION,
    updatedAt: new Date().toISOString(),
    items: progress.items || {}
  };
  const json = JSON.stringify(payload, null, 2);
  const tempPath = `${progressPath}.tmp-${process.pid}`;

  try {
    fs.writeFileSync(tempPath, json, 'utf8');
    fs.renameSync(tempPath, progressPath);
  } catch (error) {
    // Rename can fail on some Windows setups (antivirus/file lock): fall back
    // to a direct write rather than losing the whole batch's progress.
    try { fs.unlinkSync(tempPath); } catch (_) { /* ignore */ }
    fs.writeFileSync(progressPath, json, 'utf8');
    console.error(`[ScrapeProgress] Atomic write failed (${error.message}), wrote directly`);
  }

  return progressPath;
}

/** True for files the scrape readers should treat as movie JSONs. */
function isScrapeItemFile(filename) {
  return typeof filename === 'string' && filename.endsWith('.json') && !filename.startsWith('.');
}

/**
 * True when a code is already finished and must be skipped on resume.
 *
 * A progress record wins over the mere existence of {code}.json: a "partial"
 * movie also has a JSON (the successful sites' data), but still needs the
 * failed sites retried. Codes with no progress record fall back to the legacy
 * behavior (existing JSON = already scraped).
 *
 * @param {string} code - DVD code
 * @param {object} progress - progress object
 * @param {string} [scrapeDir] - scrape directory
 * @returns {boolean}
 */
function isCodeCompleted(code, progress = emptyProgress(), scrapeDir = getScrapePath()) {
  const item = progress.items && progress.items[code];
  const jsonPath = path.join(scrapeDir, `${code}.json`);

  if (item) {
    return item.status === STATUS_SUCCESS && fs.existsSync(jsonPath);
  }

  // Legacy install: no progress file yet, keep skipping files already scraped.
  return fs.existsSync(jsonPath);
}

/**
 * Split a requested batch into codes to scrape and codes to skip.
 *
 * @param {string[]} codes - all codes of the batch
 * @param {object} [progress] - progress object
 * @param {string} [scrapeDir] - scrape directory
 * @returns {{codesToScrape: string[], skipped: string[]}}
 */
function filterCodesToScrape(codes, progress = readScrapeProgress(), scrapeDir = getScrapePath()) {
  const codesToScrape = [];
  const skipped = [];

  for (const code of codes) {
    if (isCodeCompleted(code, progress, scrapeDir)) {
      skipped.push(code);
    } else {
      codesToScrape.push(code);
    }
  }

  return { codesToScrape, skipped };
}

/**
 * Build one movie's progress record. Per-site outcomes are stored verbatim
 * (status + reason) and also split into failedSites / notFoundSites so a user
 * can tell "the site errored" from "the site has no such movie".
 *
 * @param {object} params
 * @param {string} params.status - success | partial | failed
 * @param {Object<string, {status: string, reason?: string, errorKind?: string}>} params.outcomes
 * @param {string[]} [params.sources] - sites that contributed data
 * @returns {object} progress entry
 */
function buildProgressEntry({ status, outcomes = {}, sources = [] }) {
  const scrapers = {};
  const failedSites = [];
  const notFoundSites = [];

  for (const [scraperName, outcome] of Object.entries(outcomes)) {
    const siteStatus = outcome.status || SITE_ERROR;
    const reason = outcome.reason || '';
    const entry = { status: siteStatus };
    if (reason) entry.reason = reason;
    if (outcome.errorKind) entry.errorKind = outcome.errorKind;
    scrapers[scraperName] = entry;

    if (siteStatus === SITE_ERROR) {
      failedSites.push({
        scraper: scraperName,
        reason: reason || 'unknown site error',
        errorKind: outcome.errorKind || 'site_error'
      });
    } else if (siteStatus === SITE_NOT_FOUND) {
      notFoundSites.push({ scraper: scraperName, reason: reason || 'no data returned' });
    }
  }

  return {
    status,
    updatedAt: new Date().toISOString(),
    sources: sources.slice(),
    scrapers,
    failedSites,
    notFoundSites
  };
}

module.exports = {
  PROGRESS_VERSION,
  STATUS_SUCCESS,
  STATUS_PARTIAL,
  STATUS_FAILED,
  SITE_OK,
  SITE_NOT_FOUND,
  SITE_ERROR,
  emptyProgress,
  readScrapeProgress,
  writeScrapeProgress,
  isScrapeItemFile,
  isCodeCompleted,
  filterCodesToScrape,
  buildProgressEntry
};
