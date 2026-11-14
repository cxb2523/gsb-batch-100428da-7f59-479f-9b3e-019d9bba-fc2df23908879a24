import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CrawlResult } from '../types';

const { mockGot } = vi.hoisted(() => ({ mockGot: vi.fn() }));

vi.mock('got', () => ({ default: mockGot }));

import { AsyncWebCrawler } from '../crawler';
import { fetchSitemapUrls, type SitemapFetchSummary } from '../sitemap';

const ROOT = 'https://example.com/sitemap.xml';

const urlsetXml = (
  ...paths: string[]
) => `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${paths.map((p) => `  <url><loc>${p}</loc></url>`).join('\n')}
</urlset>`;

const indexXml = (...locs: string[]) => `<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${locs.map((l) => `  <sitemap><loc>${l}</loc></sitemap>`).join('\n')}
</sitemapindex>`;

function serve(entries: Record<string, string>): void {
  mockGot.mockImplementation(async (url: string) => {
    const body = entries[url];
    if (body === undefined) {
      throw new Error(`Unexpected sitemap request: ${url}`);
    }
    return { body };
  });
}

function summaryOf(
  result: Awaited<ReturnType<typeof fetchSitemapUrls>>,
): SitemapFetchSummary {
  if (Array.isArray(result) || !('sitemapSummary' in result)) {
    throw new Error('Expected a result carrying sitemapSummary');
  }
  return result.sitemapSummary;
}

const fakeResult = (url: string): CrawlResult => ({
  url,
  html: '',
  success: true,
});

beforeEach(() => {
  mockGot.mockReset();
});

describe('fetchSitemapUrls summary (default off)', () => {
  it('returns a plain URL array when the switch is not set', async () => {
    serve({ [ROOT]: urlsetXml('https://example.com/a') });

    const result = await fetchSitemapUrls(ROOT);

    expect(Array.isArray(result)).toBe(true);
    expect(result).toEqual(['https://example.com/a']);
  });

  it('returns a plain URL array when the switch is explicitly false', async () => {
    serve({ [ROOT]: urlsetXml('https://example.com/a') });

    const result = await fetchSitemapUrls(ROOT, {
      includeSitemapSummary: false,
    });

    expect(Array.isArray(result)).toBe(true);
  });
});

describe('fetchSitemapUrls summary with a urlset root', () => {
  it('reports that there are no child sitemaps', async () => {
    serve({
      [ROOT]: urlsetXml('https://example.com/a', 'https://example.com/b'),
    });

    const result = await fetchSitemapUrls(ROOT, {
      includeSitemapSummary: true,
    });
    const summary = summaryOf(result);

    expect(Array.isArray(result)).toBe(false);
    expect(summary.rootType).toBe('urlset');
    expect(summary.hasChildSitemaps).toBe(false);
    expect(summary.childSitemaps).toEqual([]);
    expect(summary.childSitemapsFetched).toBe(0);
    expect(summary.sitemapsFetched).toBe(1);
    expect(summary.childSitemapsSkippedCrossOrigin).toBe(0);
    expect(summary.childSitemapsSkippedLimit).toBe(0);
    expect(summary.truncated).toBe(false);
    expect(summary.truncatedByMaxUrls).toBe(false);
    expect(summary.truncatedByMaxSitemaps).toBe(false);
    expect(summary.urlsCollected).toBe(2);
    if (!Array.isArray(result)) {
      expect(result.urls).toEqual([
        'https://example.com/a',
        'https://example.com/b',
      ]);
    }
  });
});

describe('fetchSitemapUrls summary with a sitemap index root', () => {
  it('records fetched and cross-origin child sitemaps', async () => {
    serve({
      [ROOT]: indexXml(
        'https://example.com/sitemap-a.xml',
        'https://other.example/sitemap-b.xml',
        'https://example.com/sitemap-c.xml',
      ),
      'https://example.com/sitemap-a.xml': urlsetXml(
        'https://example.com/a1',
        'https://example.com/a2',
      ),
      'https://example.com/sitemap-c.xml': urlsetXml('https://example.com/c1'),
    });

    const result = await fetchSitemapUrls(ROOT, {
      includeSitemapSummary: true,
    });
    const summary = summaryOf(result);

    expect(Array.isArray(result)).toBe(false);
    expect(summary.rootType).toBe('sitemapindex');
    expect(summary.hasChildSitemaps).toBe(true);
    expect(summary.childSitemapsFetched).toBe(2);
    expect(summary.sitemapsFetched).toBe(3);
    expect(summary.childSitemapsSkippedCrossOrigin).toBe(1);
    expect(summary.childSitemapsSkippedLimit).toBe(0);
    expect(summary.truncated).toBe(false);
    expect(summary.childSitemaps).toEqual([
      { url: 'https://example.com/sitemap-a.xml', status: 'fetched' },
      { url: 'https://other.example/sitemap-b.xml', status: 'cross-origin' },
      { url: 'https://example.com/sitemap-c.xml', status: 'fetched' },
    ]);
    // Cross-origin child is never requested.
    expect(mockGot).not.toHaveBeenCalledWith(
      'https://other.example/sitemap-b.xml',
      expect.anything(),
    );
    if (!Array.isArray(result)) {
      expect(result.urls).toEqual([
        'https://example.com/a1',
        'https://example.com/a2',
        'https://example.com/c1',
      ]);
    }
  });

  it('still follows cross-origin children when sameOriginOnly is false', async () => {
    serve({
      [ROOT]: indexXml('https://other.example/sitemap-b.xml'),
      'https://other.example/sitemap-b.xml': urlsetXml('https://example.com/x'),
    });

    const result = await fetchSitemapUrls(ROOT, {
      includeSitemapSummary: true,
      sameOriginOnly: false,
    });
    const summary = summaryOf(result);

    expect(summary.childSitemapsSkippedCrossOrigin).toBe(0);
    expect(summary.childSitemapsFetched).toBe(1);
    if (!Array.isArray(result)) {
      expect(result.urls).toEqual(['https://example.com/x']);
    }
  });

  it('marks children not queued because of maxSitemaps', async () => {
    serve({
      [ROOT]: indexXml(
        'https://example.com/sitemap-a.xml',
        'https://example.com/sitemap-b.xml',
        'https://example.com/sitemap-c.xml',
      ),
      'https://example.com/sitemap-a.xml': urlsetXml('https://example.com/a1'),
    });

    const result = await fetchSitemapUrls(ROOT, {
      includeSitemapSummary: true,
      maxSitemaps: 2,
    });
    const summary = summaryOf(result);

    expect(summary.childSitemapsFetched).toBe(1);
    expect(summary.childSitemapsSkippedLimit).toBe(2);
    expect(summary.truncated).toBe(true);
    expect(summary.truncatedByMaxSitemaps).toBe(true);
    expect(summary.truncatedByMaxUrls).toBe(false);
    expect(summary.childSitemaps).toEqual([
      { url: 'https://example.com/sitemap-a.xml', status: 'fetched' },
      {
        url: 'https://example.com/sitemap-b.xml',
        status: 'limit-exceeded',
        limit: 'maxSitemaps',
      },
      {
        url: 'https://example.com/sitemap-c.xml',
        status: 'limit-exceeded',
        limit: 'maxSitemaps',
      },
    ]);
  });

  it('marks queued children left unfetched because maxUrls ran out', async () => {
    serve({
      [ROOT]: indexXml(
        'https://example.com/sitemap-a.xml',
        'https://example.com/sitemap-b.xml',
      ),
      'https://example.com/sitemap-a.xml': urlsetXml(
        'https://example.com/a1',
        'https://example.com/a2',
      ),
    });

    const result = await fetchSitemapUrls(ROOT, {
      includeSitemapSummary: true,
      maxUrls: 2,
    });
    const summary = summaryOf(result);

    expect(summary.truncated).toBe(true);
    expect(summary.truncatedByMaxUrls).toBe(true);
    expect(summary.childSitemapsSkippedLimit).toBe(1);
    expect(summary.childSitemaps).toEqual([
      { url: 'https://example.com/sitemap-a.xml', status: 'fetched' },
      {
        url: 'https://example.com/sitemap-b.xml',
        status: 'limit-exceeded',
        limit: 'maxUrls',
      },
    ]);
    expect(mockGot).not.toHaveBeenCalledWith(
      'https://example.com/sitemap-b.xml',
      expect.anything(),
    );
  });

  it('flags truncation when maxUrls is exhausted exactly', async () => {
    serve({
      [ROOT]: indexXml(
        'https://example.com/sitemap-a.xml',
        'https://example.com/sitemap-b.xml',
      ),
      'https://example.com/sitemap-a.xml': urlsetXml(
        'https://example.com/a1',
        'https://example.com/a2',
      ),
      'https://example.com/sitemap-b.xml': urlsetXml('https://example.com/b1'),
    });

    const result = await fetchSitemapUrls(ROOT, {
      includeSitemapSummary: true,
      maxUrls: 3,
    });
    const summary = summaryOf(result);

    expect(summary.urlsCollected).toBe(3);
    expect(summary.truncated).toBe(true);
    expect(summary.truncatedByMaxUrls).toBe(true);
    expect(summary.childSitemapsFetched).toBe(2);
    expect(summary.childSitemapsSkippedLimit).toBe(0);
  });

  it('does not flag truncation when the traversal finishes under the caps', async () => {
    serve({
      [ROOT]: indexXml('https://example.com/sitemap-a.xml'),
      'https://example.com/sitemap-a.xml': urlsetXml(
        'https://example.com/a1',
        'https://example.com/a2',
      ),
    });

    const summary = summaryOf(
      await fetchSitemapUrls(ROOT, {
        includeSitemapSummary: true,
        maxUrls: 10,
        maxSitemaps: 10,
      }),
    );

    expect(summary.urlsCollected).toBe(2);
    expect(summary.truncated).toBe(false);
  });

  it('reports a maxSitemaps stop that leaves the root unread', async () => {
    serve({ [ROOT]: indexXml('https://example.com/sitemap-a.xml') });

    const summary = summaryOf(
      await fetchSitemapUrls(ROOT, {
        includeSitemapSummary: true,
        maxSitemaps: 0,
      }),
    );

    expect(summary.childSitemapsFetched).toBe(0);
    expect(summary.truncated).toBe(true);
    expect(summary.truncatedByMaxSitemaps).toBe(true);
    expect(summary.truncatedByMaxUrls).toBe(false);
    expect(summary.urlsCollected).toBe(0);
    expect(mockGot).not.toHaveBeenCalled();
  });

  it('fetches a repeated child sitemap once and records it once', async () => {
    serve({
      [ROOT]: indexXml(
        'https://example.com/sitemap-a.xml',
        'https://example.com/sitemap-a.xml',
        'https://example.com/sitemap-a.xml#fragment',
      ),
      'https://example.com/sitemap-a.xml': urlsetXml(
        'https://example.com/a1',
        'https://example.com/a2',
      ),
    });

    const result = await fetchSitemapUrls(ROOT, {
      includeSitemapSummary: true,
    });
    const summary = summaryOf(result);

    expect(summary.childSitemaps).toEqual([
      { url: 'https://example.com/sitemap-a.xml', status: 'fetched' },
    ]);
    expect(summary.childSitemapsFetched).toBe(1);
    expect(
      mockGot.mock.calls.filter(
        ([url]) => url === 'https://example.com/sitemap-a.xml',
      ),
    ).toHaveLength(1);
    if (!Array.isArray(result)) {
      expect(result.urls).toEqual([
        'https://example.com/a1',
        'https://example.com/a2',
      ]);
    }
  });

  it('deduplicates page URLs across child sitemaps without changing coverage counts', async () => {
    serve({
      [ROOT]: indexXml(
        'https://example.com/sitemap-a.xml',
        'https://example.com/sitemap-b.xml',
      ),
      'https://example.com/sitemap-a.xml': urlsetXml(
        'https://example.com/shared',
        'https://example.com/a1',
      ),
      'https://example.com/sitemap-b.xml': urlsetXml(
        'https://example.com/shared',
        'https://example.com/b1',
      ),
    });

    const result = await fetchSitemapUrls(ROOT, {
      includeSitemapSummary: true,
    });
    const summary = summaryOf(result);

    if (!Array.isArray(result)) {
      expect(result.urls).toEqual([
        'https://example.com/shared',
        'https://example.com/a1',
        'https://example.com/b1',
      ]);
    }
    expect(summary.urlsCollected).toBe(3);
    expect(summary.childSitemapsFetched).toBe(2);
  });

  it('does not re-fetch a root sitemap that an index references', async () => {
    serve({
      [ROOT]: indexXml(ROOT, 'https://example.com/sitemap-a.xml'),
      'https://example.com/sitemap-a.xml': urlsetXml('https://example.com/a1'),
    });

    const summary = summaryOf(
      await fetchSitemapUrls(ROOT, { includeSitemapSummary: true }),
    );

    expect(summary.childSitemaps).toEqual([
      { url: 'https://example.com/sitemap-a.xml', status: 'fetched' },
    ]);
    expect(mockGot.mock.calls.filter(([url]) => url === ROOT)).toHaveLength(1);
  });

  it('returns categories and summary together when both are requested', async () => {
    serve({
      [ROOT]: indexXml(
        'https://example.com/sitemap_products_1.xml',
        'https://example.com/sitemap_pages_1.xml',
      ),
      'https://example.com/sitemap_products_1.xml': urlsetXml(
        'https://example.com/p1',
      ),
      'https://example.com/sitemap_pages_1.xml': urlsetXml(
        'https://example.com/g1',
      ),
    });

    const result = await fetchSitemapUrls(ROOT, {
      includeSitemapSummary: true,
      includeSitemapCategories: true,
    });

    expect(Array.isArray(result)).toBe(false);
    if (!Array.isArray(result) && 'sitemapCategories' in result) {
      expect(result.sitemapCategories?.categories.products.count).toBe(1);
      expect(result.sitemapCategories?.categories.pages.count).toBe(1);
    }
    expect(summaryOf(result).childSitemapsFetched).toBe(2);
  });
});

describe('fetchSitemapUrls request options are unchanged', () => {
  it('passes the configured timeout and redirect cap through', async () => {
    serve({ [ROOT]: urlsetXml('https://example.com/a') });

    await fetchSitemapUrls(ROOT, {
      timeout: 1234,
      maxRedirects: 2,
      headers: { 'User-Agent': 'test-agent' },
    });

    expect(mockGot).toHaveBeenCalledTimes(1);
    const [, requestOptions] = mockGot.mock.calls[0];
    expect(requestOptions.timeout).toEqual({ request: 1234 });
    expect(requestOptions.maxRedirects).toBe(2);
    expect(requestOptions.followRedirect).toBe(true);
    expect(requestOptions.headers['User-Agent']).toBe('test-agent');
  });

  it('applies the default timeout and redirect cap', async () => {
    serve({ [ROOT]: urlsetXml('https://example.com/a') });

    await fetchSitemapUrls(ROOT);

    const [, requestOptions] = mockGot.mock.calls[0];
    expect(requestOptions.timeout).toEqual({ request: 15_000 });
    expect(requestOptions.maxRedirects).toBe(5);
  });

  it('rejects non-HTTP(S) sitemap URLs before any request', async () => {
    await expect(fetchSitemapUrls('file:///tmp/sitemap.xml')).rejects.toThrow(
      /HTTP or HTTPS/,
    );
    expect(mockGot).not.toHaveBeenCalled();
  });
});

describe('crawlFromSitemap summary passthrough', () => {
  it('returns the summary together with sitemapCategories for an index root', async () => {
    serve({
      [ROOT]: indexXml(
        'https://example.com/sitemap_products_1.xml',
        'https://other.example/sitemap_pages_1.xml',
        'https://example.com/sitemap_pages_1.xml',
      ),
      'https://example.com/sitemap_products_1.xml': urlsetXml(
        'https://example.com/p1',
      ),
      'https://example.com/sitemap_pages_1.xml': urlsetXml(
        'https://example.com/g1',
      ),
    });

    const crawler = new AsyncWebCrawler({}, { cacheEnabled: false });
    const arunMany = vi
      .spyOn(crawler, 'arunMany')
      .mockImplementation(async (urls: string[]) => urls.map(fakeResult));

    try {
      const out = await crawler.crawlFromSitemap(
        ROOT,
        {},
        { includeSitemapSummary: true, includeSitemapCategories: true },
      );

      expect(Array.isArray(out)).toBe(false);
      if (
        !Array.isArray(out) &&
        'sitemapCategories' in out &&
        'sitemapSummary' in out
      ) {
        expect(out.results.map((r) => r.url)).toEqual([
          'https://example.com/p1',
          'https://example.com/g1',
        ]);
        expect(out.sitemapCategories.totalSitemaps).toBe(3);
        expect(out.sitemapSummary.childSitemapsFetched).toBe(2);
        expect(out.sitemapSummary.childSitemapsSkippedCrossOrigin).toBe(1);
        expect(out.sitemapSummary.truncated).toBe(false);
      }
      expect(arunMany).toHaveBeenCalledWith(
        ['https://example.com/p1', 'https://example.com/g1'],
        {},
      );
    } finally {
      crawler.clearCache();
    }
  });

  it('returns the summary alone when categories are not requested', async () => {
    serve({
      [ROOT]: indexXml('https://example.com/sitemap-a.xml'),
      'https://example.com/sitemap-a.xml': urlsetXml('https://example.com/a1'),
    });

    const crawler = new AsyncWebCrawler({}, { cacheEnabled: false });
    vi.spyOn(crawler, 'arunMany').mockImplementation(async (urls: string[]) =>
      urls.map(fakeResult),
    );

    const out = await crawler.crawlFromSitemap(
      ROOT,
      {},
      { includeSitemapSummary: true },
    );

    expect(Array.isArray(out)).toBe(false);
    if (!Array.isArray(out) && 'sitemapSummary' in out) {
      expect(out).not.toHaveProperty('sitemapCategories');
      expect(out.sitemapSummary.rootType).toBe('sitemapindex');
      expect(out.results).toHaveLength(1);
    }
  });

  it('keeps the plain array return value when the switch is off', async () => {
    serve({
      [ROOT]: indexXml('https://example.com/sitemap-a.xml'),
      'https://example.com/sitemap-a.xml': urlsetXml('https://example.com/a1'),
    });

    const crawler = new AsyncWebCrawler({}, { cacheEnabled: false });
    vi.spyOn(crawler, 'arunMany').mockImplementation(async (urls: string[]) =>
      urls.map(fakeResult),
    );

    const out = await crawler.crawlFromSitemap(ROOT);

    expect(Array.isArray(out)).toBe(true);
    expect(out).toHaveLength(1);
  });

  it('keeps the plain array return value for a urlset root even with the switch on', async () => {
    serve({ [ROOT]: urlsetXml('https://example.com/a1') });

    const crawler = new AsyncWebCrawler({}, { cacheEnabled: false });
    vi.spyOn(crawler, 'arunMany').mockImplementation(async (urls: string[]) =>
      urls.map(fakeResult),
    );

    const out = await crawler.crawlFromSitemap(
      ROOT,
      {},
      { includeSitemapSummary: true },
    );

    expect(Array.isArray(out)).toBe(true);
  });

  it('skips crawling when a summary-enabled index yields no URLs', async () => {
    serve({
      [ROOT]: indexXml('https://other.example/sitemap-b.xml'),
    });

    const crawler = new AsyncWebCrawler({}, { cacheEnabled: false });
    const arunMany = vi
      .spyOn(crawler, 'arunMany')
      .mockImplementation(async (urls: string[]) => urls.map(fakeResult));

    const out = await crawler.crawlFromSitemap(
      ROOT,
      {},
      { includeSitemapSummary: true },
    );

    expect(Array.isArray(out)).toBe(false);
    if (!Array.isArray(out) && 'sitemapSummary' in out) {
      expect(out.results).toEqual([]);
      expect(out.sitemapSummary.childSitemapsSkippedCrossOrigin).toBe(1);
    }
    expect(arunMany).not.toHaveBeenCalled();
  });
});
