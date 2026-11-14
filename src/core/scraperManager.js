#!/usr/bin/env node

/**
 * ScraperManager
 *
 * Orchestrates multiple video scrapers and merges their JSON outputs.
 * - Reads DVD IDs from library path (file names up to first space)
 * - Executes enabled scrapers sequentially
 * - Merges results based on priority rules
 * - Saves individual JSON files to data/scrape/{id}.json
 * - Emits events for real-time progress updates and interactive prompts
 */

const { spawn } = require('child_process');
const { EventEmitter } = require('events');
const fs = require('fs');
const path = require('path');
const { loadConfig, getScrapePath, getLibraryPaths } = require('./config');
const { applyGenreRules } = require('./genreFilter');
const {
  readScrapeProgress,
  writeScrapeProgress,
  isCodeCompleted,
  buildProgressEntry,
  STATUS_SUCCESS,
  STATUS_PARTIAL,
  STATUS_FAILED,
  SITE_OK,
  SITE_NOT_FOUND,
  SITE_ERROR
} = require('./scrapeProgress');

// Scrapers live in scrapers/movies; SCRAPERS_DIR lets a test point the
// orchestrator at a folder of fake scrapers instead of the real sites.
const SCRAPERS_DIR = process.env.SCRAPERS_DIR || path.join(__dirname, '../../scrapers/movies');

// Sentinel value for "scraper" params/dropdowns meaning "run every scraper
// configured in scrapers.video, in priority order, and merge like a normal
// scrape" instead of a single named scraper.
const MULTI_SCRAPER_VALUE = '__all__';

// ─────────────────────────────
// Configuration Loading
// ─────────────────────────────
// Using centralized config from config.js
// getScrapePath() is now imported from config.js and always returns data/scrape

// ─────────────────────────────
// Library Reading
// ─────────────────────────────

// IDs found anywhere in a filename: "ABP-420", "T28-587", "IBW-1010Z"...
const HYPHENATED_ID = /(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{0,5})-(\d{2,6}[A-Za-z]?)(?![A-Za-z0-9])/;
// ...or without the hyphen: "abp420" (letters only before the number)
const COMPACT_ID = /(?<![A-Za-z0-9])([A-Za-z]{2,6})(\d{2,6}[A-Za-z]?)(?![A-Za-z0-9])/;
// Same, anchored at the start of the first token and followed by its end or a
// hyphen: "Crpd-299", "Sma751", "TUE-053-720p" (not "hhd800.com@...")
const LEADING_HYPHENATED_ID = /^([A-Za-z][A-Za-z0-9]{0,5})-(\d{2,6}[A-Za-z]?)(?=$|-)/;
const LEADING_COMPACT_ID = /^([A-Za-z]{2,6})(\d{2,6}[A-Za-z]?)(?=$|-)/;

/**
 * Movie ID from a video filename, normalized to PREFIX-NUMBER when possible.
 * - The first token (split on spaces/underscores) starting with an ID wins:
 *   "Crpd-299_restored.mp4" -> "CRPD-299", "Sma751_restored.mp4" -> "SMA-751",
 *   "TUE-053-720p.mp4" -> "TUE-053".
 * - A first token that is otherwise ID-like (letters/digits/hyphens, with at
 *   least one digit) is kept as-is: "010214-514.mp4", "FC2-PPV-123456.mp4".
 * - Otherwise the ID is searched anywhere in the name: "[site] ABP-420 1080p.mkv",
 *   "site.com@ABP-420.mp4", "[site] abp420.mp4" -> "ABP-420". A hyphenated match
 *   wins over a compact one (in "hhd800.com@ABP-420" "hhd800" is not the ID).
 *
 * @param {string} filename - Video filename (with extension)
 * @returns {string} - Movie ID, or '' if none was found
 */
function extractMovieId(filename) {
  const name = filename.replace(/\.[^.]+$/, '');
  const firstToken = name.split(/[\s_]/)[0];

  const leading = firstToken.match(LEADING_HYPHENATED_ID) || firstToken.match(LEADING_COMPACT_ID);
  if (leading) {
    return `${leading[1]}-${leading[2]}`.toUpperCase();
  }
  if (/^[A-Za-z0-9][A-Za-z0-9-]*$/.test(firstToken) && /\d/.test(firstToken)) {
    return firstToken;
  }

  const match = name.match(HYPHENATED_ID) || name.match(COMPACT_ID);
  return match ? `${match[1]}-${match[2]}`.toUpperCase() : '';
}

/**
 * Extract DVD codes from the library roots
 * - Reads ONLY video files in the root of each library path (NOT recursive)
 * - Extracts the ID from the filename (see extractMovieId)
 * - Supported video extensions: .mp4, .mkv, .avi, .wmv, .mov, .flv, .m4v, .ts
 * - Used to find video files that need to be scraped
 *
 * @param {string[]} libraryPaths - Library roots containing video files
 * @returns {string[]} - Array of unique DVD codes
 */
function extractCodesFromLibrary(libraryPaths) {
  const roots = libraryPaths.filter(root => fs.existsSync(root));
  if (roots.length === 0) {
    throw new Error(`Library path not found: ${libraryPaths.join(', ')}`);
  }

  const codes = new Set();

  // Video file extensions to look for
  const videoExtensions = ['.mp4', '.mkv', '.avi', '.wmv', '.mov', '.flv', '.m4v', '.ts', '.mpg', '.mpeg'];

  for (const libraryPath of roots) {
    fs.readdirSync(libraryPath).forEach(item => {
      // Skip hidden files and directories
      if (item.startsWith('.')) {
        return;
      }

      const itemPath = path.join(libraryPath, item);
      const stats = fs.statSync(itemPath);

      // Skip directories - we only want video files in the root
      if (stats.isDirectory()) {
        return;
      }

      // Check if it's a video file
      const ext = path.extname(item).toLowerCase();
      if (!videoExtensions.includes(ext)) {
        return;
      }

      const code = extractMovieId(item);
      if (code) {
        codes.add(code);
      } else {
        console.error(`[ScraperManager] No movie ID found in filename: ${item}`);
      }
    });
  }

  return Array.from(codes);
}

// ─────────────────────────────
// Scraper Execution
// ─────────────────────────────

/**
 * Error family of a failed site, from a reason string or a scraper's own
 * `error` field. Keeps "the site said this movie doesn't exist" apart from
 * "the site failed us" (crash, rate limit, session limit, bad output).
 *
 * @param {string} reason
 * @returns {'session_limit'|'rate_limited'|'not_found'|'site_error'}
 */
function classifySiteError(reason) {
  const text = String(reason || '');
  if (/session[\s_-]*limit/i.test(text)) return 'session_limit';
  if (/rate[\s_-]*limit|error\s*1015|too\s*many\s*requests|\b429\b/i.test(text)) return 'rate_limited';
  if (/not\s*found|\b404\b|no\s+(?:such|matching)\s+(?:movie|video|item)/i.test(text)) return 'not_found';
  return 'site_error';
}

/** Last stderr line mentioning a batch-level stop (rate/session limit). */
function findBatchStopLine(stderrText) {
  const lines = String(stderrText || '')
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(line => /rate[\s_-]*limit|error\s*1015|too\s*many\s*requests|\b429\b|session[\s_-]*limit/i.test(line));
  return lines.length > 0 ? lines[lines.length - 1] : '';
}

/**
 * True when a scraper result carries actual movie data (not just `{code}`).
 * Any populated field beyond code/id/bookkeeping counts: a site may know only
 * a director or a content id for a movie, and that data must still be merged
 * instead of being reported as "no such movie". Uses isDeepEmpty so schema
 * defaults (`rating: {value: 0}`, `images: {poster: '', fanart: []}`) don't
 * count as data.
 */
function hasMeaningfulScraperData(data) {
  if (!data || data.error) return false;
  const ignored = new Set(['code', 'dvd_id', 'id', 'error']);
  return Object.keys(data).some(field => !ignored.has(field) && !isDeepEmpty(data[field]));
}

/** Outcomes marking every requested code with the same failure. */
function allErrorOutcomes(codes, errorKind, reason) {
  const outcomes = new Map();
  codes.forEach(code => outcomes.set(code, { status: SITE_ERROR, errorKind, reason }));
  return outcomes;
}

/**
 * What one site said about one code, from its stderr log lines: scrapers
 * often swallow a per-movie error and return a bare `{code}`, so the log is
 * the only place that says whether the movie is missing or the request failed.
 *
 * @param {string} code
 * @param {string} stderrText
 * @returns {{status: string, reason: string, errorKind?: string}|null}
 */
function classifyStderrForCode(code, stderrText) {
  const upper = String(code || '').toUpperCase();
  if (!upper) return null;

  // Whole-token match: "SS-1" must not match a log line about "SS-10".
  const escaped = upper.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const codeRe = new RegExp(`(?<![A-Za-z0-9])${escaped}(?![A-Za-z0-9])`, 'i');

  const lines = String(stderrText || '')
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(line => line && codeRe.test(line));

  let notFoundLine = '';
  for (const line of lines) {
    if (/not\s*found|no\s+(?:matching\s+)?(?:results?|data)|http\s*404|\b404\b/i.test(line)) {
      notFoundLine = notFoundLine || line;
      continue;
    }
    if (/error|fail|exception|timeout|timed out|rate|session|blocked|denied|http\s*[45]\d\d|status\s*[45]\d\d/i.test(line)) {
      return { status: SITE_ERROR, errorKind: classifySiteError(line), reason: line };
    }
  }

  return notFoundLine ? { status: SITE_NOT_FOUND, reason: notFoundLine } : null;
}

/**
 * Per-code outcome of one scraper run, from its stdout results, exit status
 * and stderr log:
 *   ok        -> data for that movie
 *   not_found -> the site answered but has no such movie
 *   error     -> the site failed (crash, rate limit, session limit, bad output)
 *
 * Data found before a non-zero exit is kept: a batch that scraped five movies
 * and then got rate limited must not lose those five.
 *
 * @param {string[]} codes - requested codes
 * @param {object[]} results - parsed scraper stdout
 * @param {object} [context]
 * @param {number} [context.exitCode]
 * @param {string} [context.stderrText]
 * @returns {Map<string, {status: string, reason?: string, errorKind?: string}>}
 */
function buildOutcomes(codes, results, { exitCode = 0, stderrText = '' } = {}) {
  const outcomes = new Map();
  const byCode = new Map();

  (Array.isArray(results) ? results : []).forEach(result => {
    const code = result && (result.code || result.dvd_id);
    if (code) byCode.set(String(code).toUpperCase(), result);
  });

  const batchStopReason = findBatchStopLine(stderrText);
  const batchStopKind = batchStopReason ? classifySiteError(batchStopReason) : '';

  codes.forEach(code => {
    const result = byCode.get(code.toUpperCase());

    // Data found: merge it, whatever the process did afterwards.
    if (hasMeaningfulScraperData(result)) {
      outcomes.set(code, { status: SITE_OK });
      return;
    }

    // The scraper reported its own per-movie error (e.g. {code, error:'SESSION_LIMIT'})
    if (result && result.error) {
      const reason = String(result.error);
      const errorKind = classifySiteError(reason);
      outcomes.set(code, {
        status: errorKind === 'not_found' ? SITE_NOT_FOUND : SITE_ERROR,
        ...(errorKind === 'not_found' ? {} : { errorKind }),
        reason
      });
      return;
    }

    // No stanza-level signal: the site's own log line for this code can still
    // tell an HTTP/parse failure from "no such movie".
    const logOutcome = classifyStderrForCode(code, stderrText);

    if (result) {
      // A bare {code} means "no data". With a non-zero exit, the scraper's
      // catch-all error path produced it, so it is a failure, not a miss.
      if (logOutcome && logOutcome.status === SITE_ERROR) {
        outcomes.set(code, logOutcome);
        return;
      }
      if (exitCode !== 0) {
        outcomes.set(code, {
          status: SITE_ERROR,
          errorKind: 'site_error',
          reason: `scraper exited with code ${exitCode}`
        });
        return;
      }
      outcomes.set(code, {
        status: SITE_NOT_FOUND,
        reason: (logOutcome && logOutcome.reason) || 'no data returned'
      });
      return;
    }

    // No result at all: the site stopped before reaching this code.
    if (logOutcome && logOutcome.status === SITE_ERROR) {
      outcomes.set(code, logOutcome);
      return;
    }
    if (batchStopKind && batchStopKind !== 'not_found') {
      outcomes.set(code, {
        status: SITE_ERROR,
        errorKind: batchStopKind,
        reason: batchStopReason
      });
      return;
    }
    if (exitCode !== 0) {
      outcomes.set(code, {
        status: SITE_ERROR,
        errorKind: 'site_error',
        reason: `scraper exited with code ${exitCode}`
      });
      return;
    }
    outcomes.set(code, {
      status: SITE_NOT_FOUND,
      reason: 'no result returned'
    });
  });

  return outcomes;
}

/**
 * Execute a single scraper for given codes and report, per code, whether the
 * site returned data, has no such movie, or failed.
 *
 * INTERACTIVE SCRAPER SUPPORT:
 * - Scrapers can be interactive (e.g., require user input, open browser)
 * - stderr is captured and emitted as progress events
 * - stdout is captured for JSON parsing
 * - Emits 'progress' events for real-time feedback
 * - Emits 'scraperError' events when scraper fails
 * - Supports interactive prompts via 'prompt' events
 *
 * BATCH MODE (`batch: true`):
 * - A site failure never waits for (or asks) the user; the batch continues
 * - Returns data found before a non-zero exit instead of dropping it
 *
 * @param {string} scraperName - Name of the scraper
 * @param {string[]} codes - Array of DVD codes to scrape
 * @param {EventEmitter} emitter - Event emitter for progress updates (optional)
 * @param {object} [options]
 * @param {boolean} [options.batch] - Unattended batch mode
 * @returns {Promise<{results: object[], outcomes: Map}>}
 */
function executeScraperDetailed(scraperName, codes, emitter = null, options = {}) {
  const { batch = false } = options;

  return new Promise((resolve, reject) => {
    // Scrapers are now in scrapers/movies/ subdirectory (or SCRAPERS_DIR)
    const scraperPath = path.join(SCRAPERS_DIR, scraperName, 'run.js');

    // Check if scraper exists
    if (!fs.existsSync(scraperPath)) {
      const message = `Scraper not found: ${scraperPath}`;
      console.error(`[ScraperManager] ${message}`);
      // In batch mode this is one failing site among others, not a stop:
      // report it as progress and let the remaining sites contribute.
      if (emitter) {
        emitter.emit(batch ? 'progress' : 'scraperError', batch
          ? { message }
          : { scraperName, message });
      }
      // Return minimal results for all codes
      resolve({
        results: codes.map(code => ({ code })),
        outcomes: allErrorOutcomes(codes, 'scraper_not_found', message)
      });
      return;
    }

    const message = `Executing scraper: ${scraperName}`;
    console.error(`[ScraperManager] ${message}`);
    console.error(`[ScraperManager] Codes: ${codes.join(', ')}`);
    if (emitter) emitter.emit('progress', { message: `${message} for ${codes.join(', ')}` });

    // Spawn scraper process with the same Node binary running the app
    // (process.execPath), so scrapers also start when `node` is not on PATH
    // (e.g. the bundled runtime in data/runtime).
    // - stdin: 'pipe' -> can send interactive responses
    // - stdout: 'pipe' -> capture JSON output for parsing
    // - stderr: 'pipe' -> capture progress messages and emit them
    const child = spawn(process.execPath, [scraperPath, ...codes], {
      stdio: ['pipe', 'pipe', 'pipe']
    });

    let stdout = '';
    let stdoutBuffer = '';
    let stderrText = '';

    // Collect stdout and handle interactive prompts
    child.stdout.on('data', (data) => {
      const chunk = data.toString();
      stdoutBuffer += chunk;

      // Check for interactive prompt messages (format: __PROMPT__:{"type":"confirm","message":"..."})
      let promptMatch;
      while ((promptMatch = stdoutBuffer.match(/__PROMPT__:(.+)\n/)) !== null) {
        try {
          const promptData = JSON.parse(promptMatch[1]);

          // Remove prompt from buffer
          stdoutBuffer = stdoutBuffer.replace(/__PROMPT__:.+\n/, '');

          // Emit prompt event and wait for response
          if (emitter) {
            emitter.emit('prompt', {
              scraperName,
              promptType: promptData.type || 'confirm',
              message: promptData.message || 'Waiting for user action...',
              callback: (response) => {
                // Send response to scraper via stdin
                if (child.stdin.writable) {
                  child.stdin.write(JSON.stringify({ response }) + '\n');
                }
              }
            });
          } else {
            // No emitter - send automatic confirmation (for CLI mode)
            if (child.stdin.writable) {
              child.stdin.write(JSON.stringify({ response: true }) + '\n');
            }
          }
        } catch (error) {
          console.error(`[ScraperManager] Error parsing prompt: ${error.message}`);
          break;
        }
      }

      // Scraper resolved the prompt on its own: let the UI close the dialog
      if (stdoutBuffer.includes('__PROMPT_DONE__\n')) {
        stdoutBuffer = stdoutBuffer.replace(/__PROMPT_DONE__\n/g, '');
        if (emitter) emitter.emit('promptDone', { scraperName });
      }

      // Accumulate non-prompt content for final JSON parsing
      // (stdoutBuffer now only contains non-prompt data after the while loop)
      stdout = stdoutBuffer;
    });

    // Capture stderr (progress logs) and emit as events
    child.stderr.on('data', (data) => {
      const chunk = data.toString();
      stderrText += chunk;
      const lines = chunk.split('\n').filter(l => l.trim());
      lines.forEach(line => {
        console.error(line);
        if (emitter) emitter.emit('progress', { message: line });
      });
    });

    // Ask the user whether to continue after a site-level failure. Batch mode
    // never asks: one site failing must not stop the other sites or the batch.
    const askToContinue = async (failureMessage) => {
      if (batch || !emitter) return true;
      return new Promise((resolvePrompt) => {
        emitter.emit('scraperError', {
          scraperName,
          message: failureMessage,
          callback: resolvePrompt
        });
      });
    };

    // Parse stdout, whether the process exited 0 or not: a scraper that
    // already scraped part of the batch before failing still returns it.
    const parseStdout = () => {
      try {
        const parsed = JSON.parse(stdout);
        return Array.isArray(parsed) ? parsed : [parsed];
      } catch (_) {
        return null;
      }
    };

    // Report per-code outcomes to the UI. Errors are non-blocking progress
    // lines in batch mode; "not found" keeps the existing warning event.
    const reportOutcomes = (outcomes) => {
      if (!emitter) return;
      outcomes.forEach((outcome, code) => {
        if (outcome.status === SITE_ERROR) {
          const reason = outcome.errorKind ? `${outcome.errorKind}: ${outcome.reason}` : outcome.reason;
          console.error(`[ScraperManager] ${scraperName} failed for ${code}: ${reason}`);
          emitter.emit('progress', { message: `${scraperName} failed for ${code} (${reason})` });
        } else if (outcome.status === SITE_NOT_FOUND) {
          console.error(`[ScraperManager] ${scraperName} found no data for ${code}: ${outcome.reason}`);
          emitter.emit('scraperWarning', { scraperName, code, reason: outcome.reason });
        }
      });
    };

    // Handle process exit
    child.on('close', async (code) => {
      const parsedResults = stdout.trim() ? parseStdout() : null;

      if (code !== 0) {
        const failureMessage = `Scraper ${scraperName} exited with code ${code}`;
        console.error(`[ScraperManager] ${failureMessage}`);

        if (batch) {
          // Keep whatever the scraper managed to return, mark the rest failed
          const results = parsedResults || codes.map(c => ({ code: c }));
          const outcomes = buildOutcomes(codes, parsedResults || results, { exitCode: code, stderrText });
          reportOutcomes(outcomes);
          resolve({ results, outcomes });
          return;
        }

        const shouldContinue = await askToContinue(failureMessage);
        if (!shouldContinue) {
          // User chose to stop - reject to stop scraping
          reject(new Error(`Scraping stopped by user after ${scraperName} failed`));
          return;
        }

        // Return minimal results if user chose to continue
        resolve({
          results: codes.map(c => ({ code: c })),
          outcomes: buildOutcomes(codes, [], { exitCode: code, stderrText })
        });
        return;
      }

      // Parse JSON output
      if (!parsedResults) {
        const parseMessage = `Failed to parse JSON from ${scraperName}: ${stdout.trim() ? 'invalid JSON' : 'empty output'}`;
        console.error(`[ScraperManager] ${parseMessage}`);

        if (batch) {
          const outcomes = buildOutcomes(codes, [], { stderrText });
          outcomes.forEach(outcome => {
            if (outcome.status === SITE_NOT_FOUND) {
              outcome.status = SITE_ERROR;
              outcome.errorKind = 'invalid_output';
              outcome.reason = parseMessage;
            }
          });
          reportOutcomes(outcomes);
          resolve({ results: codes.map(c => ({ code: c })), outcomes });
          return;
        }

        const shouldContinue = await askToContinue(parseMessage);
        if (!shouldContinue) {
          reject(new Error(`Scraping stopped by user after ${scraperName} failed to parse JSON`));
          return;
        }

        resolve({
          results: codes.map(c => ({ code: c })),
          outcomes: allErrorOutcomes(codes, 'invalid_output', parseMessage)
        });
        return;
      }

      const results = parsedResults;
      const message = `Scraper ${scraperName} completed successfully`;
      console.error(`[ScraperManager] ${message}`);
      if (emitter) emitter.emit('progress', { message });

      const outcomes = buildOutcomes(codes, results, { stderrText });
      reportOutcomes(outcomes);
      resolve({ results, outcomes });
    });

    // Handle process error
    child.on('error', async (error) => {
      const message = `Failed to execute ${scraperName}: ${error.message}`;
      console.error(`[ScraperManager] ${message}`);

      const shouldContinue = await askToContinue(message);
      if (!shouldContinue) {
        reject(new Error(`Scraping stopped by user after ${scraperName} failed to execute`));
        return;
      }

      resolve({
        results: codes.map(c => ({ code: c })),
        outcomes: allErrorOutcomes(codes, 'spawn_error', message)
      });
    });
  });
}

/**
 * Execute a single scraper for given codes (array-only compatibility wrapper).
 *
 * @param {string} scraperName - Name of the scraper
 * @param {string[]} codes - Array of DVD codes to scrape
 * @param {EventEmitter} emitter - Event emitter for progress updates (optional)
 * @param {object} [options] - See executeScraperDetailed
 * @returns {Promise<object[]>} - Parsed JSON array from scraper stdout
 */
async function executeScraper(scraperName, codes, emitter = null, options = {}) {
  const { results } = await executeScraperDetailed(scraperName, codes, emitter, options);
  return results;
}

// ─────────────────────────────
// Data Merging
// ─────────────────────────────
//
// NOTE: Scrapers return data in standard format (see schema.js).
// ScraperManager keeps all fields (even empty ones) to match WebUI expectations.

/**
 * Get priority order for a specific field
 *
 * @param {string} fieldName - Name of the field
 * @param {object} config - Configuration object
 * @returns {string[]} - Ordered list of scraper names for this field
 */
function getFieldPriority(fieldName, config) {
  // Check if field has explicit priority
  if (config.fieldPriorities && config.fieldPriorities[fieldName]) {
    return config.fieldPriorities[fieldName];
  }

  // Use global scraper order as fallback (new structure: config.scrapers.video)
  return (config.scrapers && config.scrapers.video) ? config.scrapers.video : [];
}

/**
 * Merge data from multiple scrapers for a single DVD code
 *
 * For each field, uses priority order from fieldPriorities (if configured)
 * or default scraper order. Picks the first non-empty value from scrapers
 * in priority order.
 *
 * @param {string} code - DVD code
 * @param {object[]} scraperResults - Array of {scraperName, data} objects
 * @param {object} config - Configuration object
 * @returns {object} - Merged object with all schema fields
 */
/**
 * Formats the movie title using the configured pattern (e.g. "{id} {title}")
 * Applied after merge so it's independent of which scraper provided the title.
 */
function formatTitle(item, config) {
  const pattern = (config.scrapeTitlePattern || "{title}").trim() || "{title}";

  const year = item.releaseDate ? item.releaseDate.split("-")[0] : "";
  const values = {
    id: item.id || "",
    contentid: item.contentId || "",
    title: item.title || "",
    alternatetitle: item.alternateTitle || "",
    label: item.label || "",
    maker: item.studio || "",
    year: year
  };

  return pattern.replace(/\{(\w+)\}/g, (match, key) => {
    const lowerKey = key.toLowerCase();
    return values.hasOwnProperty(lowerKey) ? values[lowerKey] : match;
  }).trim();
}

function isEmptyValue(value) {
  return value === null ||
         value === undefined ||
         value === '' ||
         (Array.isArray(value) && value.length === 0) ||
         (typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === 0);
}

/**
 * Union of several actor lists, in priority order: one site may know more of
 * the cast than another, so actors are summed, never dropped. Two entries are
 * the same actor when any of their names/alt names match (case-, whitespace-
 * and 2-word-order-insensitive); the first occurrence wins, later ones only
 * fill its empty fields and add alt names.
 *
 * @param {Array<object[]|undefined>} lists - Actor arrays, highest priority first
 * @returns {object[]}
 */
function mergeActors(lists) {
  const { dedupeAltNames, stripAgeSuffix } = require('../../scrapers/actors/schema');
  const actorDb = require('../../scrapers/actors/actorDb');
  const normalize = s => stripAgeSuffix(s).toLowerCase().replace(/\s+/g, ' ');
  // Romanization variants of the same Japanese name: Kudou/Kudo, Kuu/Ku, Lala/Rara
  const foldRomaji = n => n.replace(/ou|oo/g, 'o').replace(/uu/g, 'u').replace(/l/g, 'r');
  const keysOf = actor => {
    const names = [actor.name, ...(actor.altName || '').split(',')].map(normalize).filter(Boolean);
    const keys = new Set();
    if (actor.id) keys.add(`id:${actor.id}`);
    // The central actor DB bridges spellings no string rule can (Ohara Tomomi = 大原友美)
    const dbId = actorDb.resolveId(actor.name, actor.altName);
    if (dbId) keys.add(`id:${dbId}`);
    names.forEach(n => {
      [n, foldRomaji(n)].forEach(variant => {
        keys.add(variant);
        const parts = variant.split(' ');
        if (parts.length === 2) keys.add(`${parts[1]} ${parts[0]}`);
      });
    });
    return keys;
  };

  const result = [];
  const resultKeys = [];

  lists.forEach(list => {
    if (!Array.isArray(list)) return;
    list.forEach(actor => {
      if (!actor || !actor.name) return;
      const keys = keysOf(actor);
      const idx = resultKeys.findIndex(existing => [...keys].some(k => existing.has(k)));

      if (idx === -1) {
        result.push({ ...actor });
        resultKeys.push(keys);
        return;
      }

      const target = result[idx];
      Object.keys(actor).forEach(field => {
        if (field !== 'altName' && isEmptyValue(target[field]) && !isEmptyValue(actor[field])) {
          target[field] = actor[field];
        }
      });
      const altNames = [...(target.altName || '').split(','), ...(actor.altName || '').split(','), actor.name]
        .map(s => s.trim()).filter(Boolean);
      target.altName = dedupeAltNames(target.name, altNames).join(', ');
      keys.forEach(k => resultKeys[idx].add(k));
    });
  });

  return result;
}

/**
 * Actor list of a rescrape. The new cast (already summed across scrapers)
 * replaces the old one; an old actor the scrape didn't find survives only if
 * the central actor DB knows it (found by another site, or added by hand),
 * since a bare unknown name is usually a variant of one just scraped. Every
 * actor is resolved to its DB id first, so name variants of the same person
 * collapse into one. An empty scrape keeps the old cast untouched.
 *
 * @param {object[]|undefined} newActors - Actors from the new scrape
 * @param {object[]|undefined} oldActors - Actors already on the movie
 * @returns {object[]}
 */
function mergeRescrapeActors(newActors, oldActors) {
  const fresh = Array.isArray(newActors) ? newActors.filter(a => a && a.name) : [];
  const old = Array.isArray(oldActors) ? oldActors.filter(a => a && a.name) : [];
  if (fresh.length === 0) return old;

  const actorDb = require('../../scrapers/actors/actorDb');
  const withDbId = actor => {
    const { id, ...rest } = actor;
    const dbId = actorDb.resolveId(actor.name, actor.altName);
    return dbId ? { ...rest, id: dbId } : rest;
  };

  const resolvedNew = fresh.map(withDbId);
  const knownOld = old.map(withDbId).filter(a => a.id);
  return mergeActors([resolvedNew, knownOld]);
}

function mergeResults(code, scraperResults, config) {
  const { createEmptyMovie } = require('../../scrapers/movies/schema');
  const merged = createEmptyMovie(code);

  const globalOrder = (config.scrapers && config.scrapers.video) ? config.scrapers.video : [];

  // Primary scraper = first in global order that actually returned results
  const primaryName = globalOrder.find(name => scraperResults.some(r => r.scraperName === name));
  const primaryResult = primaryName ? scraperResults.find(r => r.scraperName === primaryName) : null;

  // Actors are summed across scrapers (primary, then fieldPriorities, then the rest)
  const actorPriority = getFieldPriority('actor', config).filter(n => n !== primaryName);
  const actorOrder = [
    ...(primaryResult ? [primaryResult] : []),
    ...actorPriority.map(name => scraperResults.find(r => r.scraperName === name)).filter(Boolean),
    ...scraperResults.filter(r => r.scraperName !== primaryName && !actorPriority.includes(r.scraperName))
  ];

  // Collect all available fields
  const allFields = new Set();
  scraperResults.forEach(({ data }) => {
    Object.keys(data).forEach(field => {
      if (field !== 'code' && field !== 'dvd_id' && field !== 'id' && field !== 'error') {
        allFields.add(field);
      }
    });
  });

  allFields.forEach(fieldName => {
    if (fieldName === 'actor') {
      merged.actor = mergeActors(actorOrder.map(r => r.data.actor));
      return;
    }

    // Step 1: primary scraper always wins for fields it found
    if (primaryResult && primaryResult.data[fieldName] !== undefined) {
      const value = primaryResult.data[fieldName];
      if (!isEmptyValue(value)) {
        merged[fieldName] = value;
        return;
      }
    }

    // Step 2: primary didn't have it — use fieldPriorities for remaining scrapers
    const priority = getFieldPriority(fieldName, config).filter(n => n !== primaryName);
    let foundValue = false;

    for (const scraperName of priority) {
      const scraperResult = scraperResults.find(r => r.scraperName === scraperName);
      if (scraperResult && scraperResult.data[fieldName] !== undefined) {
        const value = scraperResult.data[fieldName];
        if (!isEmptyValue(value)) {
          merged[fieldName] = value;
          foundValue = true;
          break;
        }
      }
    }

    // Step 3: fallback — any remaining scraper not yet tried
    if (!foundValue) {
      for (const scraperResult of scraperResults) {
        if (scraperResult.scraperName === primaryName) continue;
        if (priority.includes(scraperResult.scraperName)) continue;
        if (scraperResult.data[fieldName] !== undefined) {
          const value = scraperResult.data[fieldName];
          if (!isEmptyValue(value)) {
            merged[fieldName] = value;
            break;
          }
        }
      }
    }
  });

  merged.id = code;
  if (merged.title) {
    merged.title = formatTitle(merged, config);
  }
  return merged;
}

/**
 * Re-scrape helper for a single code: runs either one named scraper, or —
 * when scraperOrAll is MULTI_SCRAPER_VALUE — every scraper configured in
 * scrapers.video (priority order), merged with the same mergeResults()
 * logic as a normal multi-scraper scrape.
 *
 * @param {string} scraperOrAll - Scraper name, or MULTI_SCRAPER_VALUE
 * @param {string} code - Single DVD code to re-scrape
 * @param {EventEmitter} emitter - Event emitter for progress updates (optional)
 * @param {object} config - Loaded config (scrapers.video used for the "all" case)
 * @returns {Promise<{data: object, usedScrapers: string[]}>}
 */
async function executeScraperOrAll(scraperOrAll, code, emitter, config) {
  if (scraperOrAll !== MULTI_SCRAPER_VALUE) {
    const results = await executeScraper(scraperOrAll, [code], emitter);
    if (!results || results.length === 0) {
      throw new Error(`No results from scraper ${scraperOrAll}`);
    }
    return { data: results[0], usedScrapers: [scraperOrAll] };
  }

  const scrapersToRun = (config.scrapers && config.scrapers.video) || [];
  if (scrapersToRun.length === 0) {
    throw new Error('No scrapers configured in scrapers.video');
  }

  const scraperResults = [];
  for (const scraperName of scrapersToRun) {
    const results = await executeScraper(scraperName, [code], emitter);
    if (results && results[0]) {
      scraperResults.push({ scraperName, data: results[0] });
    }
  }

  if (scraperResults.length === 0) {
    throw new Error(`No results from any configured scraper (${scrapersToRun.join(', ')})`);
  }

  return {
    data: mergeResults(code, scraperResults, config),
    usedScrapers: scraperResults.map(r => r.scraperName)
  };
}

// ─────────────────────────────
// File Saving
// ─────────────────────────────
//
// NOTE: Scrapers are now responsible for returning data in standard format.
// See scrapers/movies/schema.js for the expected format.
// ScraperManager no longer performs field name normalization.

/**
 * Save scraped data to data/scrape/{code}.json with wrapper structure
 *
 * @param {string} code - DVD code
 * @param {object} data - Scraped and merged data
 * @param {string[]} sources - List of scraper names used
 * @param {string[]} libraryPaths - Library roots (searched in order for the video)
 * @param {string} [outputDir] - scrape output directory (defaults to data/scrape)
 */
function saveToFile(code, data, sources, libraryPaths, outputDir = getScrapePath()) {

  // Ensure output directory exists
  if (!fs.existsSync(outputDir)) {
    fs.mkdirSync(outputDir, { recursive: true });
  }

  // Find the video file in the root of a library path (NOT recursive): that's
  // the only place extractCodesFromLibrary() takes codes from. A recursive search
  // would match the video of an already organized movie with the same code (e.g.
  // a re-scrape of a better quality copy), and ScrapeSaver would then create the
  // new folder inside the existing one. Its directory is where ScrapeSaver creates
  // the movie folder, so each movie lands in the root its video came from.
  const videoExtensions = ['.mp4', '.mkv', '.avi', '.wmv', '.mov', '.flv', '.m4v', '.ts', '.mpg', '.mpeg'];

  let videoFile = '';
  for (const libraryPath of libraryPaths) {
    let items;
    try {
      items = fs.readdirSync(libraryPath, { withFileTypes: true });
    } catch (error) {
      console.error(`[ScraperManager] Cannot read directory ${libraryPath}: ${error.message}`);
      continue;
    }

    const match = items.find(entry => {
      if (!entry.isFile() || entry.name.startsWith('.')) return false;
      if (!videoExtensions.includes(path.extname(entry.name).toLowerCase())) return false;
      return extractMovieId(entry.name).toLowerCase() === code.toLowerCase();
    });
    if (match) {
      videoFile = path.join(libraryPath, match.name);
      break;
    }
  }

  // Create wrapper structure matching WebUI expected format
  // Scrapers already return data in standard format (see schema.js)
  const wrappedData = {
    scrapedAt: new Date().toISOString(),
    sources: sources || [],
    videoFile: videoFile,
    data: data
  };

  const outputPath = path.join(outputDir, `${code}.json`);

  try {
    fs.writeFileSync(outputPath, JSON.stringify(wrappedData, null, 2), 'utf-8');
    console.error(`[ScraperManager] Saved: ${outputPath}`);
  } catch (error) {
    console.error(`[ScraperManager] ERROR saving ${code}.json: ${error.message}`);
    console.error(`[ScraperManager] Error details:`, error);
    throw error; // Re-throw to make the error visible
  }
}

/**
 * Empty for carry-over purposes: like isEmptyValue, but an object/array of
 * only empty leaves counts as empty too (createEmptyMovie() always seeds
 * `images: {poster:'', fanart:[]}`, `rating: {value:0, votes:0}`, `runtime: 0`:
 * those must not mask a real previous value). 0 counts as empty here, so a
 * runtime the missing site used to provide survives the retry.
 */
function isDeepEmpty(value) {
  if (isEmptyValue(value)) return true;
  if (typeof value === 'number') return value === 0;
  if (Array.isArray(value)) return value.length === 0;
  if (value && typeof value === 'object') return Object.values(value).every(isDeepEmpty);
  return false;
}

/**
 * Fill empty fields of `target` from `fallback` (a previously merged movie).
 * Used when retrying a partial movie: the data merged from sites that already
 * succeeded before must not be lost just because they missed this run. Fresh
 * values always win; only gaps are filled, so merge priority is unchanged.
 *
 * @param {object} target - freshly merged movie (mutated)
 * @param {object|null|undefined} fallback - previous merged data
 * @returns {object} target
 */
function carryOverFields(target, fallback) {
  if (!fallback || typeof fallback !== 'object') return target;

  Object.keys(fallback).forEach(field => {
    if (field === 'id' || field === 'code') return;
    if (field === 'actor') {
      // Cast is a union in priority order: fresh actors first, old ones only
      // added when not already present (mergeActors dedupes by name/id).
      target.actor = mergeActors([target.actor, fallback.actor]);
      return;
    }
    if (isDeepEmpty(target[field]) && !isDeepEmpty(fallback[field])) {
      target[field] = fallback[field];
    }
  });

  return target;
}

/**
 * Merge one code from however many sites succeeded, and persist its progress
 * entry. A site that errored for this code is recorded but never blocks the
 * merge of the sites that did return data.
 *
 * Retries pass `previous` (the movie's existing JSON): if some site errors
 * again, fields and cast already merged from sites that succeeded before are
 * carried over instead of being dropped.
 *
 * @param {object} params
 * @param {string} params.code - DVD code
 * @param {object[]} params.scraperResults - {scraperName, data} of successful sites
 * @param {Object<string, object>} params.outcomes - site name -> outcome
 * @param {object} params.config - loaded config
 * @param {string[]} params.libraryPaths - library roots
 * @param {object} [params.previous] - existing wrapper for this code (retries)
 * @returns {{status: string, entry: object, merged: object}}
 */
function mergeAndRecordCode({ code, scraperResults, outcomes, config, libraryPaths, previous = null, scrapeDir = getScrapePath() }) {
  const merged = mergeResults(code, scraperResults, config);

  if (merged.genres && config.genreRules) {
    merged.genres = applyGenreRules(merged.genres, config.genreRules);
  }

  const usedScrapers = scraperResults.map(r => r.scraperName);
  const hasSiteError = Object.values(outcomes).some(o => o.status === SITE_ERROR);
  const hasMissingSite = Object.values(outcomes).some(o => o.status !== SITE_OK);

  // A retry that still misses a site keeps the data that site provided in an
  // earlier run (fresh fields keep priority).
  if (hasMissingSite && previous && previous.data) {
    carryOverFields(merged, previous.data);
  }

  // Only save if we have valid data (not just code/id fields)
  const hasValidData = merged.title || merged.studio || merged.releaseDate ||
                       (merged.actor && merged.actor.length > 0) ||
                       (merged.genres && merged.genres.length > 0);

  // Sites whose data came from the previous merge still count as sources.
  const sources = hasMissingSite && previous && previous.data
    ? [...new Set([...usedScrapers, ...(previous.sources || [])])]
    : usedScrapers;

  let status;
  let saveError = '';

  if (hasValidData) {
    try {
      saveToFile(code, merged, sources, libraryPaths, scrapeDir);
      // Saved data is kept even when another site failed: that movie is
      // "partial", not lost, and only the failed sites get retried.
      status = hasSiteError ? STATUS_PARTIAL : STATUS_SUCCESS;
    } catch (error) {
      status = STATUS_FAILED;
      saveError = `failed to save merged data: ${error.message}`;
    }
  } else {
    status = STATUS_FAILED;
  }

  const entry = buildProgressEntry({ status, outcomes, sources });
  if (saveError) entry.reason = saveError;

  return { status, entry, merged };
}

/**
 * Load an existing scraped movie wrapper ({scrapedAt, sources, videoFile, data}).
 *
 * @param {string} scrapeDir - scrape directory
 * @param {string} code - DVD code
 * @returns {object|null}
 */
function readExistingWrapper(scrapeDir, code) {
  const jsonPath = path.join(scrapeDir, `${code}.json`);
  try {
    const parsed = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch (_) {
    return null;
  }
}

/**
 * Main scraping function.
 *
 * Runs every enabled site for the whole batch, merges per movie, and records
 * each movie's outcome in data/scrape/.scrape-progress.json. A site error for
 * one movie (including rate/session limits) never stops the other sites from
 * contributing, and data already returned by the sites that succeeded is kept.
 *
 * @param {string[]} codes - Array of DVD codes to scrape
 * @param {EventEmitter} emitter - Event emitter for progress updates (optional)
 * @param {object} [options]
 * @param {boolean} [options.batch=true] - Unattended: one site failing never prompts
 * @param {string} [options.scrapeDir] - scrape directory (defaults to data/scrape)
 * @param {string[]} [options.skipped] - codes skipped by the resume filter (recorded only)
 * @returns {Promise<object[]>} - Array of merged results
 */
async function scrapeAll(codes, emitter = null, options = {}) {
  const { batch = true, scrapeDir = getScrapePath(), skipped = [] } = options;
  const config = loadConfig();

  // Get list of enabled scrapers (new structure: config.scrapers.video)
  const enabledScrapers = (config.scrapers && config.scrapers.video) ? config.scrapers.video : [];

  if (enabledScrapers.length === 0) {
    const message = 'No scrapers enabled in config.json';
    console.error(`[ScraperManager] ${message}`);
    if (emitter) emitter.emit('error', { message });
    return codes.map(code => ({ code }));
  }

  const message = `Enabled scrapers: ${enabledScrapers.join(', ')}`;
  console.error(`[ScraperManager] ${message}`);
  console.error(`[ScraperManager] Scraping ${codes.length} code(s): ${codes.join(', ')}`);
  if (emitter) emitter.emit('start', {
    message: `Starting scrape for ${codes.length} code(s)`,
    scrapers: enabledScrapers,
    codes
  });

  // Progress file shared with the next run: successful movies are skipped,
  // partial/failed ones are retried. Loaded once and flushed before the run
  // and after every merged movie, so an interrupted batch still leaves the
  // entries finished so far (a resumed run only redoes what is missing).
  const progress = readScrapeProgress(scrapeDir);
  progress.scrapers = enabledScrapers.slice();
  progress.lastRun = {
    startedAt: new Date().toISOString(),
    finishedAt: '',
    codes: codes.slice(),
    skipped: skipped.slice()
  };
  writeScrapeProgress(progress, scrapeDir);

  // Execute all enabled scrapers sequentially. Per code, this keeps the
  // outcome of every site: ok / not_found / error (+ kind and reason).
  const scraperOutputs = [];
  const outcomesByCode = new Map();
  codes.forEach(code => outcomesByCode.set(code, {}));

  for (const scraperName of enabledScrapers) {
    let run;
    try {
      run = await executeScraperDetailed(scraperName, codes, emitter, { batch });
    } catch (error) {
      // Only a deliberate user stop (non-batch mode) rejects. Record it as a
      // site failure for the movies not merged yet, then stop the batch.
      if (!batch) {
        progress.lastRun.finishedAt = new Date().toISOString();
        writeScrapeProgress(progress, scrapeDir);
        throw error;
      }
      run = {
        results: codes.map(code => ({ code })),
        outcomes: allErrorOutcomes(codes, 'site_error', error.message)
      };
    }

    const { results, outcomes } = run;
    scraperOutputs.push({ scraperName, results });

    codes.forEach(code => {
      const outcome = outcomes.get(code) ||
        { status: SITE_ERROR, errorKind: 'invalid_output', reason: 'no outcome reported' };
      outcomesByCode.get(code)[scraperName] = outcome;
    });
  }

  // Group successful site results by code
  const resultsByCode = {};
  codes.forEach(code => {
    resultsByCode[code] = [];
  });

  scraperOutputs.forEach(({ scraperName, results }) => {
    results.forEach(data => {
      // Match by code or dvd_id field
      const code = data.code || data.dvd_id;

      // Normalize: ensure data has 'code' field
      if (!data.code && data.dvd_id) {
        data.code = data.dvd_id;
      }

      // Find matching code (case-insensitive)
      const matchingCode = codes.find(c => c.toUpperCase() === (code || '').toUpperCase());
      if (!matchingCode || !resultsByCode[matchingCode]) return;

      // Only data-bearing results are merged; an errored/empty result must
      // not shadow the value another site provided.
      const outcome = outcomesByCode.get(matchingCode)[scraperName];
      if (!outcome || outcome.status !== SITE_OK) return;

      resultsByCode[matchingCode].push({ scraperName, data });
    });
  });

  // Merge results for each code, save files, record progress
  const finalResults = [];
  const summary = { total: codes.length, success: 0, partial: 0, failed: 0 };
  const libraryPaths = getLibraryPaths(config);

  for (const code of codes) {
    const outcomes = outcomesByCode.get(code) || {};
    // A retried movie may already hold data from sites that succeeded before.
    const previous = progress.items[code] ? readExistingWrapper(scrapeDir, code) : null;

    const { status, entry, merged } = mergeAndRecordCode({
      code,
      scraperResults: resultsByCode[code] || [],
      outcomes,
      config,
      libraryPaths,
      previous,
      scrapeDir
    });

    summary[status]++;
    progress.items[code] = entry;
    finalResults.push(merged);
    // Flush per movie: an interrupted batch keeps everything merged so far,
    // so the next run doesn't redo completed movies.
    writeScrapeProgress(progress, scrapeDir);

    if (status === STATUS_FAILED) {
      const reasons = entry.failedSites.concat(entry.notFoundSites)
        .map(f => `${f.scraper}: ${f.reason}`).join('; ');
      console.error(`[ScraperManager] ${code}: failed, no usable data${reasons ? ` (${reasons})` : ''}`);
    } else if (status === STATUS_PARTIAL) {
      const failed = entry.failedSites.map(f => `${f.scraper} (${f.errorKind})`).join(', ');
      console.error(`[ScraperManager] ${code}: partial, failed sites: ${failed}`);
    }

    if (emitter) {
      emitter.emit('scrapeProgress', {
        code,
        status,
        sources: entry.sources,
        failedSites: entry.failedSites,
        notFoundSites: entry.notFoundSites,
        summary: { ...summary }
      });
    }
  }

  progress.lastRun.finishedAt = new Date().toISOString();
  progress.lastRun.summary = summary;
  writeScrapeProgress(progress, scrapeDir);

  console.error(`[ScraperManager] Batch finished: ${summary.success} success, ` +
    `${summary.partial} partial, ${summary.failed} failed of ${summary.total}`);

  // Note: Actor scraping is now handled in routes.js, not here
  // This allows better control over the completion flow
  return finalResults;
}

// ─────────────────────────────
// CLI Entry Point
// ─────────────────────────────

async function main() {
  try {
    const config = loadConfig();

    // Extract codes from library path
    const libraryPaths = getLibraryPaths(config);

    if (libraryPaths.length === 0) {
      throw new Error('libraryPaths not specified in config.json');
    }

    console.error(`[ScraperManager] Reading library: ${libraryPaths.join(', ')}`);

    const codes = extractCodesFromLibrary(libraryPaths);

    if (codes.length === 0) {
      console.error('[ScraperManager] No files found in library');
      process.exit(0);
    }

    console.error(`[ScraperManager] Found ${codes.length} file(s) to scrape`);

    // Resume: skip movies already merged without site errors; retry the
    // partial and failed ones from the previous run.
    const scrapeDir = getScrapePath();
    const progress = readScrapeProgress(scrapeDir);
    const codesToScrape = [];
    const skipped = [];

    codes.forEach(code => {
      if (isCodeCompleted(code, progress, scrapeDir)) {
        skipped.push(code);
        console.error(`[ScraperManager] Skipping ${code} - already scraped`);
      } else {
        codesToScrape.push(code);
      }
    });

    if (codesToScrape.length === 0) {
      console.error('[ScraperManager] All files already scraped. Nothing to do.');
      process.exit(0);
    }

    console.error(`[ScraperManager] Scraping ${codesToScrape.length} file(s), skipped ${skipped.length}`);

    // Execute scraping
    const results = await scrapeAll(codesToScrape, null, { skipped });

    console.error(`[ScraperManager] Completed. Merged ${results.length} item(s), progress in ${getScrapePath()}`);

  } catch (error) {
    console.error(`[ScraperManager] Fatal error: ${error.message}`);
    process.exit(1);
  }
}

// Run if executed directly
if (require.main === module) {
  main();
}

// Export for use as module
module.exports = {
  scrapeAll,
  extractCodesFromLibrary,
  extractMovieId,
  executeScraper,
  executeScraperDetailed,
  executeScraperOrAll,
  mergeResults,
  mergeActors,
  mergeRescrapeActors,
  isEmptyValue,
  formatTitle,
  classifySiteError,
  MULTI_SCRAPER_VALUE
};
