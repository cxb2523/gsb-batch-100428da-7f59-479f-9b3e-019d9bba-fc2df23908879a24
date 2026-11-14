import { afterAll, expect, test } from 'bun:test';
import { Database } from 'bun:sqlite';
import { mkdtempSync, readdirSync, readFileSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { join, resolve } from 'path';

/**
 * End-to-end tests for the sitemap resume chain against real SQLite files.
 *
 * The schema comes from the checked-in migrations and `fetchURL` is replaced in
 * an isolated Bun process. Sitemap XML and failures are synthesized locally; no
 * Pixiv requests are made.
 */

const root = resolve('.');
const workDir = mkdtempSync(join(tmpdir(), 'pixiv-sitemap-resume-'));
const migrationsDir = resolve('prisma/migrations');

afterAll(() => {
  rmSync(workDir, { recursive: true, force: true });
});

function createDatabase(): string {
  const dir = mkdtempSync(join(workDir, 'db-'));
  const database = new Database(join(dir, 'pixiv.db'));
  const migrations = readdirSync(migrationsDir)
    .filter((name) => name !== 'migration_lock.toml')
    .sort();
  for (const name of migrations) {
    database.exec(
      readFileSync(join(migrationsDir, name, 'migration.sql'), 'utf8'),
    );
  }
  database.close();
  return join(dir, 'pixiv.db');
}

type PartScenario = {
  loc: string;
  lastmod: string;
  xml?: string;
  failure?: 'http503' | 'cloudflare' | 'html';
};

type Scenario = {
  indexParts: PartScenario[];
  /** Fail the Nth article batch write (1-based) inside a part transaction. */
  failBatchAt?: number;
  /** Abort mid-part after the Nth batch write instead of failing it. */
  abortAfterBatchAt?: number;
  existingRows?: Array<{
    tag_name: string;
    lastScraped: string;
    lastScrapedArticle: string | null;
  }>;
  /** Pre-existing legacy category watermark (as an older deployment left it). */
  legacyWatermark?: string;
};

type ScenarioResult = {
  aborted: boolean;
  error?: { name: string; message: string };
  summary?: {
    total: number;
    completed: Array<{ loc: string; lastmod: string; upserted: number }>;
    failed: Array<{ loc: string; lastmod: string; reason: string }>;
  };
  rows: Array<{
    tag_name: string;
    lastScraped: string;
    lastScrapedArticle: string | null;
  }>;
  partProgress: Array<{ loc: string; lastmod: string }>;
  watermark: string | null;
  batchesAttempted: number;
};

const script = `
  import { mock } from 'bun:test';
  const root = ${JSON.stringify(root)};
  const databasePath = process.env.DATABASE_PATH;
  const scenario = JSON.parse(process.env.SCENARIO);
  const { PrismaClient } = await import('@prisma/client');
  const prisma = new PrismaClient({ datasourceUrl: 'file:' + databasePath });
  const controller = new AbortController();
  let batchesAttempted = 0;
  const txScript = scenario.abortAfterBatchAt !== undefined ? 'cancelled' : 'boom';
  const transactional = new Proxy(prisma, {
    get(target, property) {
      if (property === '$transaction') {
        return (fn, options) => target.$transaction((tx) => {
          const txProxy = new Proxy(tx, {
            get(txTarget, txProperty) {
              if (txProperty === '$executeRawUnsafe') {
                return async (...args) => {
                  batchesAttempted++;
                  if (batchesAttempted === scenario.abortAfterBatchAt) {
                    controller.abort(new Error(txScript));
                  }
                  if (batchesAttempted === scenario.failBatchAt) {
                    throw new Error('simulated article batch write failure');
                  }
                  return txTarget.$executeRawUnsafe(...args);
                };
              }
              const value = txTarget[txProperty];
              return typeof value === 'function' ? value.bind(txTarget) : value;
            },
          });
          return fn(txProxy);
        }, options);
      }
      const value = target[property];
      return typeof value === 'function' ? value.bind(target) : value;
    },
  });
  mock.module(root + '/src/index.ts', () => ({ prisma: transactional }));
  mock.module(root + '/src/fetch/fetchURL.ts', () => ({
    closeSession: async () => undefined,
    fetchURL: async (url) => {
      if (url.endsWith('/sitemap.xml')) {
        const entries = scenario.indexParts.map((part) =>
          '<sitemap><loc>' + part.loc + '</loc><lastmod>' + part.lastmod +
          '</lastmod></sitemap>').join('');
        return { status: 200, data: '<sitemapindex>' + entries + '</sitemapindex>' };
      }
      const part = scenario.indexParts.find((candidate) => candidate.loc === url);
      if (!part) throw new Error('Unexpected sitemap part');
      if (part.failure === 'http503') {
        const { HttpError } = await import(root + '/src/fetch/errors.ts');
        throw new HttpError(503, 'Service unavailable');
      }
      if (part.failure === 'cloudflare') {
        const { CloudflareError } = await import(root + '/src/fetch/errors.ts');
        throw new CloudflareError();
      }
      if (part.failure === 'html') return { status: 200, data: '<html>error</html>' };
      return { status: 200, data: part.xml };
    },
  }));
  for (const row of scenario.existingRows ?? []) {
    await prisma.$executeRawUnsafe(
      'INSERT INTO PixivArticle (tag_name, summary, updated_at, main_illst_url, view_count, illust_count, check_count, related_tags, lastScraped, lastScrapedArticle) VALUES (?,?,?,?,?,?,?,?,?,?)',
      row.tag_name, '', 'old', '', 0, 0, 0, '[]', row.lastScraped, row.lastScrapedArticle,
    );
  }
  if (scenario.legacyWatermark !== undefined) {
    await prisma.$executeRawUnsafe(
      "INSERT INTO scrapeProgress (category, newestScraped) VALUES ('sitemap', ?)",
      scenario.legacyWatermark,
    );
  }
  const { scrapeSitemap } = await import(root + '/src/scrape/scrapeSitemap.ts');
  let summary;
  let error;
  try {
    summary = await scrapeSitemap(controller.signal);
  } catch (caught) {
    error = { name: caught.name, message: caught.message };
    summary = caught.sitemapSweep;
  }
  const rows = await prisma.$queryRawUnsafe(
    'SELECT tag_name, lastScraped, lastScrapedArticle FROM PixivArticle ORDER BY tag_name');
  const partProgress = await prisma.$queryRawUnsafe(
    'SELECT loc, lastmod FROM sitemapPartProgress ORDER BY loc');
  const watermarkRow = await prisma.$queryRawUnsafe(
    "SELECT newestScraped FROM scrapeProgress WHERE category = 'sitemap'");
  await prisma.$disconnect();
  console.log(JSON.stringify({
    aborted: controller.signal.aborted,
    error,
    summary,
    rows,
    partProgress,
    watermark: watermarkRow[0]?.newestScraped ?? null,
    batchesAttempted,
  }));
`;

function urlset(tags: Array<{ tag: string; lastmod: string }>): string {
  return `<urlset>${tags
    .map(
      ({ tag, lastmod }) =>
        `<url><loc>https://dic.pixiv.net/a/${encodeURIComponent(tag)}</loc><lastmod>${lastmod}</lastmod></url>`,
    )
    .join('')}</urlset>`;
}

async function runScenario(
  scenario: Scenario,
  databasePath = createDatabase(),
): Promise<ScenarioResult> {
  const child = Bun.spawn([process.execPath, '--eval', script], {
    cwd: root,
    env: {
      ...process.env,
      SCENARIO: JSON.stringify(scenario),
      DATABASE_PATH: databasePath,
    },
    stdout: 'pipe',
    stderr: 'pipe',
  });
  const [code, stdout, stderr] = await Promise.all([
    child.exited,
    new Response(child.stdout).text(),
    new Response(child.stderr).text(),
  ]);
  if (code !== 0) throw new Error(`${stdout}\n${stderr}`);
  return JSON.parse(stdout.trim().split('\n').at(-1)!);
}

const PART_A = 'https://dic.pixiv.net/sitemap/part/a';
const PART_B = 'https://dic.pixiv.net/sitemap/part/b';
const PART_C = 'https://dic.pixiv.net/sitemap/part/c';
const LM = '2026-11-01T00:00:00+09:00';

const parts = (): PartScenario[] => [
  {
    loc: PART_A,
    lastmod: LM,
    xml: urlset([{ tag: 'alpha', lastmod: LM }]),
  },
  {
    loc: PART_B,
    lastmod: LM,
    xml: urlset([{ tag: 'beta', lastmod: LM }]),
  },
  {
    loc: PART_C,
    lastmod: LM,
    xml: urlset([{ tag: 'gamma', lastmod: LM }]),
  },
];

test('one failed part does not abandon the round and is reported with its reason', async () => {
  const scenarioParts = parts();
  scenarioParts[1] = {
    ...scenarioParts[1],
    xml: undefined,
    failure: 'http503',
  };
  const databasePath = createDatabase();

  const result = await runScenario({ indexParts: scenarioParts }, databasePath);

  expect(result.summary).toEqual({
    total: 3,
    completed: [
      { loc: PART_A, lastmod: LM, upserted: 1 },
      { loc: PART_C, lastmod: LM, upserted: 1 },
    ],
    failed: [{ loc: PART_B, lastmod: LM, reason: 'fetch-failed (HTTP 503)' }],
  });
  // The underlying typed error still reaches the caller after the summary.
  expect(result.error?.name).toBe('HttpError');
  // Only the parts that finished wrote rows and ledger entries.
  expect(result.rows.map((row) => row.tag_name)).toEqual(['alpha', 'gamma']);
  expect(result.partProgress.map((row) => row.loc)).toEqual([PART_A, PART_C]);
  // The coarse watermark only advances after an entirely successful round.
  expect(result.watermark).not.toBe(LM);

  // A rerun selects only the unfinished part; finished parts are not refetched.
  const rerunParts = parts();
  const rerun = await runScenario({ indexParts: rerunParts }, databasePath);
  expect(rerun.error).toBeUndefined();
  expect(rerun.summary).toEqual({
    total: 1,
    completed: [{ loc: PART_B, lastmod: LM, upserted: 1 }],
    failed: [],
  });
  expect(rerun.rows.map((row) => row.tag_name).sort()).toEqual([
    'alpha',
    'beta',
    'gamma',
  ]);
  expect(rerun.partProgress.map((row) => row.loc)).toEqual([
    PART_A,
    PART_B,
    PART_C,
  ]);
  expect(rerun.watermark).toBe(LM);
});

test('a part whose lastmod advanced is selected again even after completing', async () => {
  const databasePath = createDatabase();
  await runScenario({ indexParts: parts() }, databasePath);

  const advanced = '2026-11-02T00:00:00+09:00';
  const updatedParts = parts();
  updatedParts[0] = {
    loc: PART_A,
    lastmod: advanced,
    xml: urlset([{ tag: 'alpha', lastmod: advanced }]),
  };
  const result = await runScenario({ indexParts: updatedParts }, databasePath);
  expect(result.summary?.completed).toEqual([
    { loc: PART_A, lastmod: advanced, upserted: 1 },
  ]);
  expect(result.summary?.failed).toEqual([]);
});

test('a malformed shard keeps its SitemapSchemaError and other parts still complete', async () => {
  const scenarioParts = parts();
  scenarioParts[0] = { ...scenarioParts[0], xml: undefined, failure: 'html' };
  const result = await runScenario({ indexParts: scenarioParts });

  expect(result.error?.name).toBe('SitemapSchemaError');
  expect(result.summary?.failed).toEqual([
    expect.objectContaining({ loc: PART_A }),
  ]);
  expect(result.summary?.failed[0].reason).toStartWith('parse-failed');
  expect(result.summary?.completed.map((part) => part.loc)).toEqual([
    PART_B,
    PART_C,
  ]);
  expect(result.watermark).not.toBe(LM);
});

test('a single part whose write fails leaves no half-batch rows and no progress', async () => {
  const bigPart = urlset(
    Array.from({ length: 120 }, (_, index) => ({
      tag: `tag-${String(index).padStart(3, '0')}`,
      lastmod: LM,
    })),
  );
  const databasePath = createDatabase();
  const result = await runScenario(
    {
      indexParts: [{ loc: PART_A, lastmod: LM, xml: bigPart }],
      failBatchAt: 2,
    },
    databasePath,
  );

  expect(result.batchesAttempted).toBe(2);
  expect(result.summary?.failed[0].reason).toContain(
    'simulated article batch write failure',
  );
  // The first batch was attempted but the whole part rolled back.
  expect(result.rows).toEqual([]);
  expect(result.partProgress).toEqual([]);
  expect(result.watermark).not.toBe(LM);

  // The failed part is selected again on the next run and then commits fully.
  const retry = await runScenario(
    { indexParts: [{ loc: PART_A, lastmod: LM, xml: bigPart }] },
    databasePath,
  );
  expect(retry.error).toBeUndefined();
  expect(retry.rows).toHaveLength(120);
  expect(retry.partProgress).toEqual([{ loc: PART_A, lastmod: LM }]);
});

test('cancellation rolls back the in-flight part and records no progress for it', async () => {
  const databasePath = createDatabase();
  const result = await runScenario(
    {
      indexParts: [
        {
          loc: PART_A,
          lastmod: LM,
          xml: urlset(
            Array.from({ length: 120 }, (_, index) => ({
              tag: `tag-cancel-${index}`,
              lastmod: LM,
            })),
          ),
        },
      ],
      abortAfterBatchAt: 1,
    },
    databasePath,
  );

  expect(result.aborted).toBe(true);
  expect(result.rows).toEqual([]);
  expect(result.partProgress).toEqual([]);
});

test('known WAF tags are ignored and existing rows keep the dedup condition', async () => {
  const databasePath = createDatabase();
  const wafTag = '</title><svg onload=alert();>';
  const scenario: Scenario = {
    indexParts: [
      {
        loc: PART_A,
        lastmod: LM,
        xml: urlset([
          { tag: wafTag, lastmod: LM },
          { tag: 'inserted', lastmod: LM },
          { tag: 'unchanged', lastmod: LM },
        ]),
      },
    ],
    existingRows: [
      {
        tag_name: wafTag,
        lastScraped: '1',
        lastScrapedArticle: null,
      },
      {
        // Already scraped individually at a newer time; must not be overwritten.
        tag_name: 'unchanged',
        lastScraped: '1',
        lastScrapedArticle: '9999999999999',
      },
    ],
  };
  const result = await runScenario(scenario, databasePath);

  expect(result.rows.map((row) => row.tag_name)).toEqual([
    'inserted',
    'unchanged',
  ]);
  expect(
    result.rows.find((row) => row.tag_name === 'unchanged')?.lastScraped,
  ).toBe('1');
  expect(result.summary?.completed[0].upserted).toBe(2);
});

test('an existing database with only the legacy watermark is not scraped from scratch', async () => {
  const databasePath = createDatabase();
  // Older deployments recorded "all parts at or below this lastmod are done".
  const result = await runScenario(
    { indexParts: parts(), legacyWatermark: LM },
    databasePath,
  );

  expect(result.error).toBeUndefined();
  expect(result.summary).toEqual({ total: 0, completed: [], failed: [] });
  expect(result.partProgress.map((row) => row.loc)).toEqual([
    PART_A,
    PART_B,
    PART_C,
  ]);
  expect(result.rows).toEqual([]);
});
