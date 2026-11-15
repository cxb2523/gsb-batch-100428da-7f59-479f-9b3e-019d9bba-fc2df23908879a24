/** Sync the focused Apify and CoreClaw catalogs used by this repository. */
const fs = require('fs');
const path = require('path');
const http = require('http');
const https = require('https');

const ROOT = process.env.SYNC_CATALOG_ROOT || path.join(__dirname, '..');
const config = JSON.parse(fs.readFileSync(path.join(__dirname, 'repo.config.json'), 'utf8'));

/**
 * Upstream URLs. The configured addresses from repo.config.json can be
 * overridden with environment variables (for example to point at a local
 * temporary HTTP service during offline testing). Both http and https
 * overrides are supported.
 */
const SOURCES = [
  {
    heading: 'Apify catalog',
    url: process.env.APIFY_SOURCE_URL || config.megaListSource,
    destination: 'catalog/README.md',
  },
  {
    heading: 'CoreClaw catalog',
    url: process.env.CORECLAW_SOURCE_URL || config.coreclawSource,
    destination: 'catalog/coreclaw.md',
  },
];

function fetchText(url) {
  return new Promise((resolve, reject) => {
    const client = url.startsWith('https:') ? https : http;
    client.get(url, { headers: { 'User-Agent': 'API-directory-sync/1.0' } }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => {
        if (res.statusCode !== 200) return reject(new Error(`${res.statusCode} while fetching ${url}`));
        resolve(body);
      });
    }).on('error', reject);
  });
}

function writeIfChanged(filePath, content) {
  const previous = fs.existsSync(filePath) ? fs.readFileSync(filePath, 'utf8') : null;
  if (previous === content) return false;
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, content, 'utf8');
  return true;
}

/**
 * Sync a single source. Never throws: every failure is turned into a
 * structured outcome so that one failing source cannot prevent the other
 * source from being processed.
 *
 * Outcome statuses:
 *   updated        - fetched and written because the content changed
 *   unchanged      - fetched, but the content matches what is already on disk
 *   kept-existing  - failed, yet an old local file exists and was left intact
 *   failed         - failed and there was no local file to fall back on
 */
async function syncOne(source) {
  const filePath = path.join(ROOT, source.destination);
  const hasExisting = fs.existsSync(filePath);
  // Snapshot the current content up front so a failed write can be rolled
  // back instead of leaving a partially written catalog behind.
  const previous = hasExisting ? fs.readFileSync(filePath, 'utf8') : null;

  let content;
  try {
    const body = await fetchText(source.url);
    content = `<!-- Synced from ${source.url} -->\n\n${body.trim()}\n`;
  } catch (error) {
    if (hasExisting) {
      return { heading: source.heading, status: 'kept-existing', reason: error.message };
    }
    return { heading: source.heading, status: 'failed', reason: error.message };
  }

  try {
    const changed = writeIfChanged(filePath, content);
    return { heading: source.heading, status: changed ? 'updated' : 'unchanged' };
  } catch (error) {
    if (hasExisting) {
      // Put the old content back in case the failed write truncated the file.
      try {
        fs.writeFileSync(filePath, previous, 'utf8');
      } catch (restoreError) {
        return {
          heading: source.heading,
          status: 'kept-existing',
          reason: `${error.message}; restore of existing catalog failed: ${restoreError.message}`,
        };
      }
      return { heading: source.heading, status: 'kept-existing', reason: error.message };
    }
    return { heading: source.heading, status: 'failed', reason: error.message };
  }
}

function printOutcome(outcome) {
  const line = `${outcome.heading}: ${outcome.status}`;
  if (outcome.reason) {
    console.log(`${line} (${outcome.reason})`);
  } else {
    console.log(line);
  }
}

async function main() {
  const outcomes = [];
  // Sequential on purpose: each source is handled independently and a failure
  // of one never skips the other.
  for (const source of SOURCES) {
    const outcome = await syncOne(source);
    outcomes.push(outcome);
    printOutcome(outcome);
  }

  // Non-zero only when a source failed with no existing local file to keep.
  if (outcomes.some((outcome) => outcome.status === 'failed')) {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
