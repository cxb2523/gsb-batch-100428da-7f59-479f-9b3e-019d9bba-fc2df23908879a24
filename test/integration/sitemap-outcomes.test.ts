import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import AjvModule from 'ajv';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { auditWebsite } from '../../src/analyze/audit.js';
import { crawl } from '../../src/crawl/crawler.js';
import { RunStore } from '../../src/crawl/store.js';
import { DEFAULT_POLICY } from '../../src/net/ssrf.js';
import { generateSchemas } from '../../scripts/schema.mjs';
import { page, startServer, text, type Route, type TestServer } from '../helpers/server.js';

type Validate = ((data: unknown) => boolean) & { errors?: unknown };
const Ajv = ((AjvModule as unknown as { default?: unknown }).default ?? AjvModule) as new (o: object) => { addKeyword(k: string): void; compile(s: object): Validate };

const policy = { ...DEFAULT_POLICY, allowPrivate: true };
const URLSET = (locs: string[]) =>
  `<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${locs.map((l) => `<url><loc>${l}</loc></url>`).join('')}</urlset>`;
const INDEX = (locs: string[]) =>
  `<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${locs.map((l) => `<sitemap><loc>${l}</loc></sitemap>`).join('')}</sitemapindex>`;
const xml = (body: string): Route => ({ headers: { 'content-type': 'application/xml' }, body });

let server: TestServer;
let ws: string;
const PAGES = ['page-a', 'page-b', 'page-c'];

beforeAll(async () => {
  const routes: Record<string, Route> = {};
  server = await startServer(routes);
  const o = server.url;
  routes['/robots.txt'] = { headers: { 'content-type': 'text/plain' }, body: `User-agent: *\nAllow: /\nSitemap: ${o}/sitemap-index.xml\n` };
  routes['/sitemap-index.xml'] = xml(INDEX([`${o}/sm-ok.xml`, `${o}/sm-gone.xml`]));
  routes['/sm-ok.xml'] = xml(URLSET([`${o}/`, ...PAGES.map((p) => `${o}/${p}`)]));
  // /sm-gone.xml has no route: the server answers 404.
  routes['/'] = { body: page({ title: 'Home page of the test site', body: `<h1>Home</h1>${text(120)}` }) };
  for (const p of PAGES) routes[`/${p}`] = { body: page({ title: `Page ${p} of the test site`, body: `<h1>${p}</h1>${text(120)}` }) };
  ws = fs.mkdtempSync(path.join(os.tmpdir(), 'searvex-sm-outcomes-'));
});

afterAll(async () => {
  await server.close();
  fs.rmSync(ws, { recursive: true, force: true });
});

const hits = (p: string) => server.hits.get(p) ?? 0;
const readSummary = (runId: string) => JSON.parse(fs.readFileSync(path.join(ws, '.searvex', 'runs', runId, 'crawl-summary.json'), 'utf8'));
const readCheckpoint = (runId: string) => JSON.parse(fs.readFileSync(path.join(ws, '.searvex', 'runs', runId, 'checkpoint.json'), 'utf8'));

describe('per-sitemap outcomes', () => {
  it('records each child sitemap: ok with url count, failed with reason; pages of the ok one are crawled', async () => {
    const store = new RunStore(ws, RunStore.newRunId('sm'));
    const result = await crawl(server.url + '/', { policy, delayMs: 0, timeoutMs: 3000, concurrency: 2 }, store);

    // URLs of the successful child sitemap were enqueued and crawled.
    for (const p of PAGES) {
      const rec = result.pages.find((r) => r.url === `${server.url}/${p}`);
      expect(rec?.status, p).toBe(200);
      expect(rec?.inSitemap, p).toBe(true);
    }

    const byUrl = new Map(result.sitemap.outcomes.map((o) => [o.url, o]));
    expect(byUrl.get(`${server.url}/sitemap-index.xml`)).toMatchObject({ status: 'ok', urlCount: 0 });
    expect(byUrl.get(`${server.url}/sm-ok.xml`)).toMatchObject({ status: 'ok', urlCount: PAGES.length + 1 });
    const gone = byUrl.get(`${server.url}/sm-gone.xml`);
    expect(gone?.status).toBe('fetch-failed');
    expect(gone?.urlCount).toBe(0);
    expect(gone?.error).toMatch(/HTTP 404/);

    // The outcomes are part of crawl-summary.json in the run directory.
    const summary = readSummary(store.runId);
    const summaryGone = (summary.sitemap.outcomes as Array<{ url: string; status: string; error?: string }>).find((o) => o.url === `${server.url}/sm-gone.xml`);
    expect(summaryGone?.status).toBe('fetch-failed');
    expect(summaryGone?.error).toMatch(/HTTP 404/);
    const summaryOk = (summary.sitemap.outcomes as Array<{ url: string; urlCount: number }>).find((o) => o.url === `${server.url}/sm-ok.xml`);
    expect(summaryOk?.urlCount).toBe(PAGES.length + 1);
  });

  it('after an interrupt, --resume does not re-request succeeded sitemaps and retries a failed one once', async () => {
    const runId = RunStore.newRunId('sm');
    // The hit counter is shared with the other tests: assert deltas from this test's baseline.
    const base = { index: hits('/sitemap-index.xml'), ok: hits('/sm-ok.xml'), gone: hits('/sm-gone.xml') };
    const pageBase = new Map(['/', ...PAGES.map((x) => `/${x}`)].map((p) => [p, hits(p)]));
    // Run 1: abort after the first fetched page; sitemap collection has already finished.
    const ac = new AbortController();
    const first = await crawl(server.url + '/', { policy, delayMs: 0, timeoutMs: 3000, concurrency: 1, stop: ac.signal, onProgress: (p) => p.fetched >= 1 && ac.abort('signal') }, new RunStore(ws, runId));
    expect(first.interrupted).toBe('signal');
    expect(hits('/sitemap-index.xml')).toBe(base.index + 1);
    expect(hits('/sm-ok.xml')).toBe(base.ok + 1);
    expect(hits('/sm-gone.xml')).toBe(base.gone + 1);

    // Run 2 (--resume): the index and sm-ok are done and skipped; sm-gone gets its one retry.
    const before = { index: hits('/sitemap-index.xml'), ok: hits('/sm-ok.xml'), gone: hits('/sm-gone.xml') };
    const second = await crawl(server.url + '/', { policy, delayMs: 0, timeoutMs: 3000, concurrency: 1 }, new RunStore(ws, runId));
    expect(second.interrupted).toBeUndefined();
    expect(hits('/sitemap-index.xml')).toBe(before.index);
    expect(hits('/sm-ok.xml')).toBe(before.ok);
    expect(hits('/sm-gone.xml')).toBe(before.gone + 1);

    // No page is fetched twice across the two runs.
    const pageHits = ['/', ...PAGES.map((x) => `/${x}`)].map((p) => hits(p) - (pageBase.get(p) ?? 0));
    expect(pageHits).toEqual(['/', ...PAGES].map(() => 1));
    expect(second.pages.length).toBe(PAGES.length + 1);

    // The resumed run still knows every sitemap URL and the merged outcomes.
    const byUrl = new Map(second.sitemap.outcomes.map((o) => [o.url, o]));
    expect(byUrl.get(`${server.url}/sm-ok.xml`)).toMatchObject({ status: 'ok', urlCount: PAGES.length + 1 });
    expect(byUrl.get(`${server.url}/sm-gone.xml`)?.status).toBe('fetch-failed');
    expect(second.sitemap.urls.some((u) => u.loc === `${server.url}/page-a`)).toBe(true);
    expect(second.pages.find((r) => r.url === `${server.url}/page-b`)?.inSitemap).toBe(true);

    // The checkpoint persists the sitemap state: done set + attempt counts (failed retried at most once).
    const cp = readCheckpoint(runId);
    expect(cp.sitemap.done).toContain(`${server.url}/sm-ok.xml`);
    expect(cp.sitemap.done).toContain(`${server.url}/sitemap-index.xml`);
    expect(cp.sitemap.done).not.toContain(`${server.url}/sm-gone.xml`);
    expect(cp.sitemap.attempts[`${server.url}/sm-gone.xml`]).toBe(2);

    // A third run does not request the failed sitemap again (one retry used up).
    await crawl(server.url + '/', { policy, delayMs: 0, timeoutMs: 3000, concurrency: 1 }, new RunStore(ws, runId));
    expect(hits('/sm-gone.xml')).toBe(before.gone + 1);
    expect(hits('/sm-ok.xml')).toBe(before.ok);
  });

  it('report.json carries the outcomes as optional crawl.sitemapOutcomes and still validates against the strict schema', async () => {
    const { report } = await auditWebsite(server.url + '/', { workspace: ws, crawl: { policy, delayMs: 0, timeoutMs: 3000 }, probes: false });
    const outcomes = report.crawl.sitemapOutcomes ?? [];
    const byUrl = new Map(outcomes.map((o) => [o.url, o]));
    // The report distinguishes "enqueued from this sitemap" from "never queued: the sitemap failed".
    expect(byUrl.get(`${server.url}/sm-ok.xml`)).toMatchObject({ status: 'ok', urlCount: PAGES.length + 1 });
    expect(byUrl.get(`${server.url}/sm-gone.xml`)).toMatchObject({ status: 'fetch-failed', urlCount: 0 });
    expect(byUrl.get(`${server.url}/sm-gone.xml`)?.error).toMatch(/HTTP 404/);

    const ajv = new Ajv({ allErrors: true });
    ajv.addKeyword('x-searvex-schema-version');
    const validate = ajv.compile(generateSchemas({ strict: true }).schemas['report']!);
    expect(validate(report) ? null : JSON.stringify(validate.errors, null, 1).slice(0, 2000), 'report validates against the strict schema').toBeNull();
  }, 60000);

  it('a checkpoint without sitemap state (written by an older version) resumes as "nothing succeeded"', async () => {
    const runId = RunStore.newRunId('sm');
    const store = new RunStore(ws, runId);
    store.ensure();
    const start = `${server.url}/`;
    // Old-format checkpoint: queue + seen only, no sitemap field.
    store.writeJson('checkpoint.json', { queue: [{ url: start, depth: 0 }], seen: [start] });
    const before = hits('/sm-ok.xml');
    const result = await crawl(server.url + '/', { policy, delayMs: 0, timeoutMs: 3000, concurrency: 1 }, store);
    expect(result.pages.find((p) => p.url === start)?.status).toBe(200);
    // Sitemaps were collected again without an error, and the failed child is recorded.
    expect(hits('/sm-ok.xml')).toBe(before + 1);
    expect(result.sitemap.outcomes.find((o) => o.url === `${server.url}/sm-gone.xml`)?.status).toBe('fetch-failed');
    expect(readCheckpoint(runId).sitemap.done).toContain(`${server.url}/sm-ok.xml`);
  });
});
