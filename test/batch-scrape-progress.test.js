#!/usr/bin/env node

/**
 * Batch scrape progress / resume tests — no network, no real sites.
 *
 * Creates a temp workspace with fake scrapers (success, empty data, non-zero
 * exit, rate limit) and checks:
 *   1. one site failing never stops the others for the same movie;
 *   2. data from successful sites is kept (partial movies are saved);
 *   3. progress file records per-movie status + failed sites and reasons,
 *      keeping "site error" apart from "site has no such movie";
 *   4. a second run skips fully merged movies and retries only partial/failed
 *      ones, updating (not staling) the progress file;
 *   5. data/scrape/{id}.json keeps its existing wrapper format.
 *
 * Run: node test/batch-scrape-progress.test.js
 */

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { EventEmitter } = require('events');

const ROOT = path.join(__dirname, '..');

// ─────────────────────────────
// Temp workspace + fake scrapers
// ─────────────────────────────

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'javinizer-batch-test-'));
const scrapersDir = path.join(tmpRoot, 'scrapers');
const libraryDir = path.join(tmpRoot, 'library');
const scrapeDir = path.join(tmpRoot, 'scrape');
const configPath = path.join(tmpRoot, 'config.json');
const invocationLog = path.join(tmpRoot, 'invocations.log');

fs.mkdirSync(libraryDir, { recursive: true });
fs.mkdirSync(scrapeDir, { recursive: true });

// Log every invocation (scraper name + codes) so a test can prove the exact
// execution range of a run.
function writeFakeScraper(name, body) {
  const dir = path.join(scrapersDir, name);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'run.js'), `#!/usr/bin/env node
'use strict';
const fs = require('fs');
const LOG = ${JSON.stringify(invocationLog)};
const MODE = process.env.FAKE_MODE || 'first';
const codes = process.argv.slice(2);
fs.appendFileSync(LOG, JSON.stringify({ scraper: ${JSON.stringify(name)}, mode: MODE, codes }) + '\\n');
${body}
`, 'utf8');
}

// Success site: returns data for AAA-001 and BBB-002 (plus DDD-004, used by
// the retry-carry-over test). 'retry' mode also covers CCC-003 so a failed
// movie can recover; 'silent' mode returns nothing at all.
writeFakeScraper('siteA', `
const want = MODE === 'retry' ? ['AAA-001', 'BBB-002', 'CCC-003']
  : MODE === 'silent' ? []
  : ['AAA-001', 'BBB-002', 'DDD-004'];
const results = codes.filter(c => want.includes(c)).map(c => ({
  code: c,
  title: 'Site A title for ' + c,
  studio: 'Studio A',
  releaseDate: '2024-01-01',
  genres: ['Drama'],
  coverUrl: 'https://example.invalid/' + c + '.jpg'
}));
console.log(JSON.stringify(results));
process.exit(0);
`);

// Empty-data site: always answers "no such movie" (bare {code}) for BBB-002.
writeFakeScraper('siteEmpty', `
const results = codes.map(c => ({ code: c }));
console.error('[siteEmpty] Not found: ' + codes.join(', '));
console.log(JSON.stringify(results));
process.exit(0);
`);

// Flaky site: returns data for AAA-001, then exits non-zero. stderr carries a
// rate-limit line, as the real scrapers do when a site blocks the batch.
writeFakeScraper('siteLimited', `
if (MODE === 'retry') {
  // Second run: all requested codes recover.
  console.log(JSON.stringify(codes.map(c => ({
    code: c, title: 'Recovered title for ' + c, studio: 'Studio R'
  }))));
  process.exit(0);
}
const partial = codes.filter(c => c === 'AAA-001').map(c => ({
  code: c, title: 'Site Limited title for ' + c, label: 'Label L'
}));
console.log(JSON.stringify(partial));
console.error('[siteLimited] Rate limited by the site (error 1015), stopping the batch. Try again later.');
process.exit(3);
`);

// Session-limit site: per-movie error objects instead of a crash.
writeFakeScraper('siteSession', `
const results = codes.map(c => MODE === 'retry'
  ? { code: c, title: 'Session recovered for ' + c }
  : { code: c, error: 'Session limit reached' });
console.log(JSON.stringify(results));
process.exit(0);
`);

const CODES = ['AAA-001', 'BBB-002', 'CCC-003'];

fs.writeFileSync(configPath, JSON.stringify({
  libraryPaths: [libraryDir],
  mode: 'scrape',
  language: 'en',
  scrapers: { video: ['siteA', 'siteEmpty', 'siteLimited', 'siteSession'], actors: { enabled: false } },
  genreRules: '',
  fieldPriorities: {}
}, null, 2), 'utf8');

// Scraped video placeholder so saveToFile can resolve a videoFile.
CODES.forEach(code => fs.writeFileSync(path.join(libraryDir, `${code}.mp4`), ''));

process.env.CONFIG_PATH = configPath;
process.env.SCRAPERS_DIR = scrapersDir;

// Load only after the environment points at the temp workspace: config.js
// reads CONFIG_PATH at require time and scraperManager reads SCRAPERS_DIR.
const scraperManager = require(path.join(ROOT, 'src/core/scraperManager'));
const progressStore = require(path.join(ROOT, 'src/core/scrapeProgress'));

// ─────────────────────────────
// Helpers
// ─────────────────────────────

function readInvocations() {
  if (!fs.existsSync(invocationLog)) return [];
  return fs.readFileSync(invocationLog, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
}

function countInvocations(rows) {
  const counts = {};
  rows.forEach(row => {
    row.codes.forEach(code => {
      const key = `${row.scraper}:${code}`;
      counts[key] = (counts[key] || 0) + 1;
    });
  });
  return counts;
}

function readProgress() {
  return JSON.parse(fs.readFileSync(path.join(scrapeDir, '.scrape-progress.json'), 'utf8'));
}

function readMovie(code) {
  return JSON.parse(fs.readFileSync(path.join(scrapeDir, `${code}.json`), 'utf8'));
}

function collectEvents(emitter) {
  const events = [];
  ['start', 'progress', 'scraperWarning', 'scraperError', 'scrapeProgress', 'error'].forEach(name => {
    emitter.on(name, data => events.push({ name, data }));
  });
  return events;
}

const tests = [];
function test(name, fn) { tests.push({ name, fn }); }

// ─────────────────────────────
// 1. First run: per-site failures are isolated
// ─────────────────────────────

test('first run merges despite site failures and records per-movie status', async () => {
  process.env.FAKE_MODE = 'first';
  const emitter = new EventEmitter();
  const events = collectEvents(emitter);

  await scraperManager.scrapeAll(CODES, emitter, { scrapeDir });

  const progress = readProgress();
  assert.strictEqual(progress.version, 1);
  assert.deepStrictEqual(progress.scrapers, ['siteA', 'siteEmpty', 'siteLimited', 'siteSession']);

  // AAA-001: siteA + siteLimited returned data; siteEmpty has no data;
  // siteSession hit its session limit -> partial, data from the rest kept.
  const aaa = progress.items['AAA-001'];
  assert.strictEqual(aaa.status, 'partial', 'AAA-001 must be partial');
  assert.strictEqual(aaa.scrapers.siteA.status, 'ok');
  assert.strictEqual(aaa.scrapers.siteEmpty.status, 'not_found');
  assert.strictEqual(aaa.scrapers.siteLimited.status, 'ok');
  assert.strictEqual(aaa.scrapers.siteSession.status, 'error');
  assert.strictEqual(aaa.scrapers.siteSession.errorKind, 'session_limit');
  assert.deepStrictEqual(aaa.failedSites.map(f => f.scraper), ['siteSession']);
  assert.deepStrictEqual(aaa.notFoundSites.map(f => f.scraper), ['siteEmpty']);

  // The merged file keeps siteA/siteLimited data and the usual wrapper.
  const aaaFile = readMovie('AAA-001');
  assert.deepStrictEqual(Object.keys(aaaFile).sort(), ['data', 'scrapedAt', 'sources', 'videoFile']);
  assert.strictEqual(aaaFile.data.id, 'AAA-001');
  assert.strictEqual(aaaFile.data.title, 'Site A title for AAA-001');
  assert.strictEqual(aaaFile.data.label, 'Label L');
  assert.deepStrictEqual(aaaFile.sources, ['siteA', 'siteLimited']);
  assert.ok(aaaFile.videoFile.endsWith('AAA-001.mp4'));

  // BBB-002: siteLimited exited 3 with a rate-limit line before returning it.
  const bbb = progress.items['BBB-002'];
  assert.strictEqual(bbb.status, 'partial');
  assert.strictEqual(bbb.scrapers.siteEmpty.status, 'not_found');
  assert.strictEqual(bbb.scrapers.siteLimited.status, 'error');
  assert.strictEqual(bbb.scrapers.siteLimited.errorKind, 'rate_limited');
  assert.ok(/rate limited/i.test(bbb.scrapers.siteLimited.reason));

  // CCC-003: no site returned data; the crashed site's stop marks it failed.
  const ccc = progress.items['CCC-003'];
  assert.strictEqual(ccc.status, 'failed');
  assert.strictEqual(ccc.scrapers.siteA.status, 'not_found');
  assert.strictEqual(ccc.scrapers.siteLimited.errorKind, 'rate_limited');
  assert.strictEqual(ccc.scrapers.siteSession.errorKind, 'session_limit');

  // Every site ran for every code in the first batch.
  const counts = countInvocations(readInvocations());
  ['siteA', 'siteEmpty', 'siteLimited', 'siteSession'].forEach(site => {
    CODES.forEach(code => assert.strictEqual(counts[`${site}:${code}`], 1, `${site} ran for ${code}`));
  });

  // One progress event per movie, with its final status.
  const progressEvents = events.filter(e => e.name === 'scrapeProgress');
  assert.strictEqual(progressEvents.length, CODES.length);
  assert.deepStrictEqual(progressEvents.map(e => e.data.status), ['partial', 'partial', 'failed']);

  // Batch mode must not ask the user to continue after a site failure.
  assert.strictEqual(events.filter(e => e.name === 'scraperError').length, 0);

  // Summary in the progress file.
  assert.deepStrictEqual(progress.lastRun.summary, { total: 3, success: 0, partial: 2, failed: 1 });
  assert.ok(progress.lastRun.startedAt && progress.lastRun.finishedAt);
});

// ─────────────────────────────
// 2. Resume: skip completed, retry the rest
// ─────────────────────────────

test('second run skips complete movies and retries only partial/failed ones', async () => {
  // Build a fully successful movie (AAA-999) by running a clean batch first.
  process.env.FAKE_MODE = 'retry';
  fs.writeFileSync(path.join(libraryDir, 'AAA-999.mp4'), '');
  const emitter = new EventEmitter();
  await scraperManager.scrapeAll(['AAA-999'], emitter, { scrapeDir });

  const progressBefore = readProgress();
  assert.strictEqual(progressBefore.items['AAA-999'].status, 'success');

  const invocationsBefore = readInvocations().length;

  // Resume exactly like the CLI / route does.
  const progress = progressStore.readScrapeProgress(scrapeDir);
  const { codesToScrape, skipped } = progressStore.filterCodesToScrape(
    [...CODES, 'AAA-999'], progress, scrapeDir);

  assert.deepStrictEqual(skipped, ['AAA-999'], 'fully merged movie is skipped');
  assert.deepStrictEqual(codesToScrape, CODES, 'partial/failed movies are retried');

  // Second run over the retried subset.
  await scraperManager.scrapeAll(codesToScrape, new EventEmitter(), { scrapeDir, skipped });

  // Only the retried movies were executed this time: no site ran for AAA-999.
  const newInvocations = readInvocations().slice(invocationsBefore);
  newInvocations.forEach(row => {
    assert.ok(!row.codes.includes('AAA-999'), `${row.scraper} must not re-run AAA-999`);
  });

  const after = readProgress();

  // AAA-999 untouched; retried movies now complete with no failed sites.
  assert.deepStrictEqual(after.items['AAA-999'], progressBefore.items['AAA-999']);
  ['AAA-001', 'BBB-002', 'CCC-003'].forEach(code => {
    const item = after.items[code];
    assert.strictEqual(item.status, 'success', `${code} recovered`);
    assert.deepStrictEqual(item.failedSites, [], `${code} has no stale failed sites`);
    assert.strictEqual(Object.values(item.scrapers).every(s => s.status !== 'error'), true,
      `${code} has no stale site errors`);
  });

  // The old failures did not linger as a permanent gap.
  assert.deepStrictEqual(after.lastRun.summary, { total: 3, success: 3, partial: 0, failed: 0 });
  assert.deepStrictEqual(after.lastRun.skipped, ['AAA-999']);

  // A third resume pass has nothing left to do.
  const third = progressStore.filterCodesToScrape([...CODES, 'AAA-999'], after, scrapeDir);
  assert.strictEqual(third.codesToScrape.length, 0);
  assert.deepStrictEqual(third.skipped.sort(), [...CODES, 'AAA-999'].sort());
});

// ─────────────────────────────
// 3. Retry keeps earlier data when a site fails again
// ─────────────────────────────

test('a retry that still fails a site preserves data merged earlier', async () => {
  const code = 'DDD-004';
  fs.writeFileSync(path.join(libraryDir, `${code}.mp4`), '');

  // First pass: siteA has the movie, siteLimited is rate limited.
  process.env.FAKE_MODE = 'first';
  await scraperManager.scrapeAll([code], new EventEmitter(), { scrapeDir });
  let item = readProgress().items[code];
  assert.strictEqual(item.status, 'partial');
  assert.strictEqual(readMovie(code).data.title, 'Site A title for ' + code);

  // Second pass: siteA now has nothing for it, siteLimited still rate limited.
  // The earlier title must survive instead of being dropped.
  process.env.FAKE_MODE = 'silent';
  await scraperManager.scrapeAll([code], new EventEmitter(), { scrapeDir });

  item = readProgress().items[code];
  assert.strictEqual(item.status, 'partial');
  assert.strictEqual(readMovie(code).data.title, 'Site A title for ' + code,
    'data from a previously successful site is kept on retry');
  assert.strictEqual(readMovie(code).data.studio, 'Studio A', 'studio is kept on retry');
  assert.deepStrictEqual(readMovie(code).data.genres, ['Drama'], 'genres are kept on retry');
  assert.strictEqual(readMovie(code).data.coverUrl, 'https://example.invalid/' + code + '.jpg',
    'cover URL is kept on retry');
  assert.ok(readMovie(code).sources.includes('siteA'), 'previous source is preserved');
  // The failed site is still the only one recorded as failing (nothing stale).
  assert.deepStrictEqual(item.failedSites.map(f => f.scraper).sort(), ['siteLimited', 'siteSession']);
});

// ─────────────────────────────
// 4. Progress file hygiene
// ─────────────────────────────

test('progress file is dot-prefixed and excluded from scrape items', () => {
  assert.ok(fs.existsSync(path.join(scrapeDir, '.scrape-progress.json')));
  assert.strictEqual(progressStore.isScrapeItemFile('.scrape-progress.json'), false);
  assert.strictEqual(progressStore.isScrapeItemFile('AAA-001.json'), true);
  assert.strictEqual(progressStore.isScrapeItemFile('notes.txt'), false);
});

test('corrupted progress file degrades to legacy JSON-existence resume', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'javinizer-progress-broken-'));
  fs.writeFileSync(path.join(dir, '.scrape-progress.json'), '{ not json', 'utf8');
  fs.writeFileSync(path.join(dir, 'EEE-005.json'), '{}', 'utf8');

  const progress = progressStore.readScrapeProgress(dir);
  assert.deepStrictEqual(progress.items, {});
  // Legacy behavior: an existing movie JSON still counts as done.
  assert.strictEqual(progressStore.isCodeCompleted('EEE-005', progress, dir), true);
  assert.strictEqual(progressStore.isCodeCompleted('FFF-006', progress, dir), false);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('error classification separates site errors from missing data', () => {
  assert.strictEqual(scraperManager.classifySiteError('Rate limited by the site'), 'rate_limited');
  assert.strictEqual(scraperManager.classifySiteError('Cloudflare error 1015'), 'rate_limited');
  assert.strictEqual(scraperManager.classifySiteError('HTTP 429'), 'rate_limited');
  assert.strictEqual(scraperManager.classifySiteError('Session limit reached'), 'session_limit');
  assert.strictEqual(scraperManager.classifySiteError('Video not found (404)'), 'not_found');
  assert.strictEqual(scraperManager.classifySiteError('connect ECONNREFUSED'), 'site_error');
});

// ─────────────────────────────
// 5. Merge rules / output format unchanged
// ─────────────────────────────

test('mergeResults keeps field priority, primary winner and actor union', () => {
  const config = {
    scrapers: { video: ['siteA', 'siteB'] },
    fieldPriorities: { title: ['siteB', 'siteA'], genres: ['siteA', 'siteB'] },
    scrapeTitlePattern: '{id} {title}'
  };
  // Numeric/URL fields are left raw; the title pattern is applied after merge.
  const merged = scraperManager.mergeResults('ZZZ-001', [
    { scraperName: 'siteA', data: { code: 'ZZZ-001', title: 'A title', studio: 'A studio', genres: ['Drama'], plot: 'A plot', actor: [{ name: 'Alice' }] } },
    { scraperName: 'siteB', data: { code: 'ZZZ-001', title: 'B title', studio: 'B studio', genres: ['Romance'], plot: 'B plot', actor: [{ name: 'Bob' }] } }
  ], config);

  // Primary (first in scrapers.video) always wins a field it found — that is
  // the pre-existing rule, unchanged by the batch refactor.
  assert.strictEqual(merged.studio, 'A studio', 'primary scraper wins normal fields');
  assert.strictEqual(merged.title, 'ZZZ-001 A title', 'primary title + title pattern');
  // fieldPriorities still pick between non-primary scrapers.
  assert.deepStrictEqual(merged.genres, ['Drama'], 'genres priority keeps siteA first');
  // Actors are summed, never dropped
  assert.deepStrictEqual(merged.actor.map(a => a.name).sort(), ['Alice', 'Bob']);
  assert.strictEqual(merged.id, 'ZZZ-001');
});

test('fieldPriorities break ties between non-primary scrapers', () => {
  const config = {
    scrapers: { video: ['siteA', 'siteB', 'siteC'] },
    fieldPriorities: { plot: ['siteC', 'siteB'] },
    scrapeTitlePattern: '{title}'
  };
  const merged = scraperManager.mergeResults('ZZZ-002', [
    { scraperName: 'siteA', data: { code: 'ZZZ-002', title: 'A title' } },
    { scraperName: 'siteB', data: { code: 'ZZZ-002', plot: 'B plot' } },
    { scraperName: 'siteC', data: { code: 'ZZZ-002', plot: 'C plot' } }
  ], config);

  assert.strictEqual(merged.plot, 'C plot', 'fieldPriority order decides non-primary fields');
});

// ─────────────────────────────
// 6. CLI entry point resumes the same way
// ─────────────────────────────

test('CLI run skips fully merged movies and retries only the gaps', async () => {
  const { spawnSync } = require('child_process');
  const cliTmp = fs.mkdtempSync(path.join(os.tmpdir(), 'javinizer-cli-test-'));
  const cliScrapers = path.join(cliTmp, 'scrapers');
  const cliLibrary = path.join(cliTmp, 'library');
  const cliConfig = path.join(cliTmp, 'config.json');
  const cliLog = path.join(cliTmp, 'invocations.log');

  fs.mkdirSync(cliLibrary, { recursive: true });
  fs.mkdirSync(path.join(cliScrapers, 'cliSite'), { recursive: true });

  // cliSite returns data for GGG-001, but nothing for GGG-002 until mode=all.
  fs.writeFileSync(path.join(cliScrapers, 'cliSite', 'run.js'), `
const fs = require('fs');
const codes = process.argv.slice(2);
fs.appendFileSync(${JSON.stringify(cliLog)}, JSON.stringify(codes) + '\\n');
const want = process.env.CLI_MODE === 'all' ? codes : codes.filter(c => c === 'GGG-001');
console.log(JSON.stringify(want.map(c => ({ code: c, title: 'CLI title ' + c }))));
`);
  ['GGG-001', 'GGG-002'].forEach(code => fs.writeFileSync(path.join(cliLibrary, `${code}.mp4`), ''));
  fs.writeFileSync(cliConfig, JSON.stringify({
    libraryPaths: [cliLibrary], mode: 'scrape', language: 'en',
    scrapers: { video: ['cliSite'], actors: { enabled: false } }, fieldPriorities: {}
  }), 'utf8');

  const cliScrapeDir = path.join(cliTmp, 'data', 'scrape'); // getScrapePath() = cwd/data/scrape

  const runCli = (mode) => spawnSync(process.execPath, [path.join(ROOT, 'src/core/scraperManager.js')], {
    cwd: cliTmp, // keeps the CLI's data/scrape inside this temp workspace
    env: { ...process.env, CONFIG_PATH: cliConfig, SCRAPERS_DIR: cliScrapers, CLI_MODE: mode },
    encoding: 'utf8'
  });

  // First run: GGG-001 succeeds; GGG-002 has no data -> failed.
  assert.strictEqual(runCli('partial').status, 0);
  const firstProgress = JSON.parse(fs.readFileSync(path.join(cliScrapeDir, '.scrape-progress.json'), 'utf8'));
  assert.strictEqual(firstProgress.items['GGG-001'].status, 'success');
  assert.strictEqual(firstProgress.items['GGG-002'].status, 'failed');

  // Second run: only GGG-002 may be requested from the site.
  fs.writeFileSync(cliLog, '');
  assert.strictEqual(runCli('all').status, 0);
  const secondRunCodes = fs.readFileSync(cliLog, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
  assert.deepStrictEqual(secondRunCodes, [['GGG-002']], 'CLI retried only the failed movie');

  const secondProgress = JSON.parse(fs.readFileSync(path.join(cliScrapeDir, '.scrape-progress.json'), 'utf8'));
  assert.strictEqual(secondProgress.items['GGG-002'].status, 'success', 'gap is closed');
  assert.strictEqual(secondProgress.items['GGG-001'].status, 'success', 'completed movie untouched');

  // Third run: nothing to do.
  fs.writeFileSync(cliLog, '');
  const third = runCli('all');
  assert.ok(/Nothing to do/.test(third.stderr));
  assert.strictEqual(fs.readFileSync(cliLog, 'utf8').trim(), '', 'no site was invoked');

  fs.rmSync(cliTmp, { recursive: true, force: true });
});

// ─────────────────────────────
// Runner
// ─────────────────────────────

(async () => {
  // mergeActors() lazily opens data/actors-index.db; remember whether the
  // test created it so a real user database is never deleted.
  const actorDbPath = path.join(ROOT, 'data', 'actors-index.db');
  const hadActorDb = fs.existsSync(actorDbPath);

  let failed = 0;
  for (const { name, fn } of tests) {
    try {
      await fn();
      console.log(`  ok   ${name}`);
    } catch (error) {
      failed++;
      console.error(`  FAIL ${name}`);
      console.error(`       ${error.message}`);
    }
  }

  try { fs.rmSync(tmpRoot, { recursive: true, force: true }); } catch (_) { /* ignore */ }
  if (!hadActorDb) {
    try { fs.rmSync(actorDbPath, { force: true }); } catch (_) { /* ignore */ }
  }

  console.log(failed === 0 ? `\n${tests.length} test(s) passed` : `\n${failed}/${tests.length} test(s) failed`);
  process.exit(failed === 0 ? 0 : 1);
})();
