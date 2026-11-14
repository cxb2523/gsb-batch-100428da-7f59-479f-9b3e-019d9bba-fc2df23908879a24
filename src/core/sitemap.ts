import * as cheerio from 'cheerio';
import got from 'got';
import { URL } from 'node:url';
import { normalizeUrl } from '../utils/url';

/**
 * Options for fetching and parsing sitemaps.
 */
export interface SitemapOptions {
  /**
   * Request timeout in milliseconds.
   * @default 15_000
   */
  timeout?: number;

  /**
   * Maximum number of URLs to collect (stops after this many).
   * @default no limit
   */
  maxUrls?: number;

  /**
   * Maximum number of sitemap index files to follow (prevents runaway recursion).
   * @default 50
   */
  maxSitemaps?: number;

  /**
   * If true, only include URLs from the same origin as the sitemap.
   * @default true
   */
  sameOriginOnly?: boolean;

  /**
   * Custom headers (e.g. User-Agent) for sitemap requests.
   */
  headers?: Record<string, string>;

  /**
   * AbortSignal to cancel the operation.
   */
  signal?: AbortSignal;

  /**
   * Maximum number of redirects to follow.
   * @default 5
   */
  maxRedirects?: number;

  /**
   * If true and the root document is a sitemap index, the return value includes
   * a breakdown of child sitemaps by category (e.g. products, pages, blogs, collections).
   * @default false
   */
  includeSitemapCategories?: boolean;

  /**
   * If true, the return value includes a `sitemapSummary` describing what the
   * traversal actually covered: how many child sitemaps were fetched, how many
   * were skipped because they were cross-origin, whether collection stopped early
   * because `maxUrls` or `maxSitemaps` was reached, and the outcome of every
   * discovered child sitemap.
   *
   * When the root document is a plain urlset, the summary reports that there were
   * no child sitemaps. When this option is false (the default) the return value is
   * unchanged.
   * @default false
   */
  includeSitemapSummary?: boolean;
}

/**
 * Category breakdown for a sitemap index (e.g. products, pages, blogs, collections).
 */
export interface SitemapCategoryInfo {
  count: number;
  urls: string[];
}

/**
 * Result of categorizing a sitemap index by type (products, pages, blogs, etc.).
 */
export interface SitemapIndexCategories {
  /** Category name -> count and list of sitemap URLs in that category */
  categories: Record<string, SitemapCategoryInfo>;
  /** Total number of child sitemaps in the index */
  totalSitemaps: number;
}

/**
 * Extracts a category label from a sitemap URL (e.g. sitemap_products_1.xml -> "products").
 * Matches common patterns: sitemap_<category>_N.xml or sitemap_<category>.xml.
 */
export function getSitemapCategoryFromUrl(sitemapUrl: string): string {
  try {
    const pathname = new URL(sitemapUrl).pathname;
    const match = pathname.match(/sitemap_([a-z0-9]+)(?:_\d+)?\.xml/i);
    return match ? match[1].toLowerCase() : 'other';
  } catch {
    return 'other';
  }
}

const DEFAULT_TIMEOUT = 15_000;
const DEFAULT_MAX_SITEMAPS = 50;
const DEFAULT_MAX_REDIRECTS = 5;

/**
 * Fetches a URL with secure defaults (HTTPS preferred, timeout, redirect limit).
 */
async function fetchXml(
  url: string,
  options: {
    timeout: number;
    headers?: Record<string, string>;
    signal?: AbortSignal;
    maxRedirects: number;
  },
): Promise<string> {
  const parsed = new URL(url);
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') {
    throw new Error(`Sitemap URL must use HTTP or HTTPS: ${url}`);
  }

  const response = await got(url, {
    timeout: { request: options.timeout },
    followRedirect: true,
    maxRedirects: options.maxRedirects,
    headers: {
      'User-Agent':
        options.headers?.['User-Agent'] ??
        'Mozilla/5.0 (compatible; FlyScrape-Sitemap/1.0)',
      Accept: 'application/xml, text/xml, */*',
      ...options.headers,
    },
    signal: options.signal,
    https: { rejectUnauthorized: true },
  });

  return response.body;
}

/**
 * Parses sitemap XML and returns either page URLs or child sitemap URLs.
 * Exported for testing.
 */
export function parseSitemapXml(
  xml: string,
  baseUrl: string,
): { type: 'urlset'; urls: string[] } | { type: 'sitemapindex'; urls: string[] } {
  const $ = cheerio.load(xml, { xml: true });

  // Sitemaps may use default namespace; select with namespace or local name
  const locs = $('loc');
  const urls: string[] = [];

  locs.each((_, el) => {
    const text = $(el).text().trim();
    if (text) urls.push(text);
  });

  // If we have sitemap > loc, it's a sitemap index; if url > loc, it's urlset
  const sitemapLocs = $('sitemap loc');
  const urlLocs = $('url loc');

  if (sitemapLocs.length > 0) {
    const list: string[] = [];
    sitemapLocs.each((_, el) => {
      const text = $(el).text().trim();
      if (text) list.push(text);
    });
    return { type: 'sitemapindex', urls: list };
  }

  if (urlLocs.length > 0) {
    const list: string[] = [];
    urlLocs.each((_, el) => {
      const text = $(el).text().trim();
      if (text) list.push(text);
    });
    return { type: 'urlset', urls: list };
  }

  // Fallback: any <loc> (some sitemaps omit namespace or use different structure)
  if (urls.length > 0) {
    // Heuristic: if any URL ends with common sitemap patterns, treat as index
    const looksLikeIndex = urls.some(
      (u) =>
        /\.xml(?:\?|$)/.test(u) ||
        u.toLowerCase().includes('sitemap'),
    );
    return looksLikeIndex
      ? { type: 'sitemapindex', urls }
      : { type: 'urlset', urls };
  }

  return { type: 'urlset', urls: [] };
}

function resolveUrl(url: string, baseUrl: string): string {
  try {
    return new URL(url, baseUrl).href;
  } catch {
    return url;
  }
}

/**
 * Fetches a sitemap index URL (one request only) and returns categorized counts
 * of child sitemaps, e.g. { products: { count: 6, urls: [...] }, pages: { count: 6, urls: [...] }, ... }.
 * Use this to get a breakdown like "products (6), pages (6), blogs (12), collections (6)" without
 * following into each child sitemap.
 */
export async function getSitemapIndexCategories(
  sitemapIndexUrl: string,
  options: Pick<SitemapOptions, 'timeout' | 'headers' | 'signal' | 'maxRedirects'> = {},
): Promise<SitemapIndexCategories> {
  const timeout = options.timeout ?? DEFAULT_TIMEOUT;
  const maxRedirects = options.maxRedirects ?? DEFAULT_MAX_REDIRECTS;

  const xml = await fetchXml(sitemapIndexUrl, {
    timeout,
    headers: options.headers,
    signal: options.signal,
    maxRedirects,
  });

  const base = sitemapIndexUrl.replace(/\/[^/]*$/, '/');
  const parsed = parseSitemapXml(xml, base);

  if (parsed.type !== 'sitemapindex') {
    return { categories: {}, totalSitemaps: 0 };
  }

  const categories: Record<string, SitemapCategoryInfo> = {};
  for (const u of parsed.urls) {
    const resolved = resolveUrl(u, base);
    const category = getSitemapCategoryFromUrl(resolved);
    if (!categories[category]) {
      categories[category] = { count: 0, urls: [] };
    }
    categories[category].count += 1;
    categories[category].urls.push(resolved);
  }

  return {
    categories,
    totalSitemaps: parsed.urls.length,
  };
}

/**
 * Outcome of a child sitemap discovered while traversing a sitemap index.
 * - `fetched`: the child sitemap was fetched (and its URLs collected).
 * - `cross-origin`: skipped because it was on a different origin than the root.
 * - `limit-exceeded`: never fetched because `maxUrls` or `maxSitemaps` stopped
 *   the traversal first.
 */
export interface SitemapChildSitemapResult {
  /** Resolved URL of the child sitemap. */
  url: string;
  status: 'fetched' | 'cross-origin' | 'limit-exceeded';
  /** Which limit stopped this child from being fetched (only when status is `limit-exceeded`). */
  limit?: 'maxUrls' | 'maxSitemaps';
}

/** Type of the root sitemap document that was fetched. */
export type SitemapRootType = 'sitemapindex' | 'urlset' | 'unknown';

/**
 * Describes what a single `fetchSitemapUrls` call actually covered.
 */
export interface SitemapFetchSummary {
  /** Type of the root document. `urlset` means the root listed page URLs directly. */
  rootType: SitemapRootType;
  /** True when the root document listed at least one child sitemap. */
  hasChildSitemaps: boolean;
  /** Number of child sitemaps actually fetched. */
  childSitemapsFetched: number;
  /** Total sitemap documents fetched, including the root document. */
  sitemapsFetched: number;
  /** Number of child sitemaps skipped because they were cross-origin. */
  childSitemapsSkippedCrossOrigin: number;
  /** Number of child sitemaps never fetched because a limit was reached. */
  childSitemapsSkippedLimit: number;
  /** True when collection stopped early due to either `maxUrls` or `maxSitemaps`. */
  truncated: boolean;
  /** True when `maxUrls` was exhausted. */
  truncatedByMaxUrls: boolean;
  /** True when `maxSitemaps` was reached. */
  truncatedByMaxSitemaps: boolean;
  /** Number of page URLs collected. */
  urlsCollected: number;
  /** One record per distinct child sitemap, in discovery order (empty for a urlset root). */
  childSitemaps: SitemapChildSitemapResult[];
}

export type FetchSitemapUrlsResult =
  | string[]
  | { urls: string[]; sitemapCategories: SitemapIndexCategories }
  | { urls: string[]; sitemapSummary: SitemapFetchSummary }
  | {
      urls: string[];
      sitemapCategories: SitemapIndexCategories;
      sitemapSummary: SitemapFetchSummary;
    };

/**
 * Recursively fetches sitemap(s) and collects all page URLs.
 * Handles sitemap index files and deduplicates by normalized URL.
 * When includeSitemapCategories is true and the root is a sitemap index, returns
 * { urls, sitemapCategories } with a per-category count (e.g. products, pages, blogs, collections).
 * When includeSitemapSummary is true, the result also carries a sitemapSummary
 * describing traversal coverage (child sitemaps fetched/skipped, truncation flags
 * and the outcome of every child sitemap). The default return value is unchanged.
 */
export async function fetchSitemapUrls(
  sitemapUrl: string,
  options: SitemapOptions = {},
): Promise<FetchSitemapUrlsResult> {
  const timeout = options.timeout ?? DEFAULT_TIMEOUT;
  const maxUrls = options.maxUrls ?? Number.POSITIVE_INFINITY;
  const maxSitemaps = options.maxSitemaps ?? DEFAULT_MAX_SITEMAPS;
  const sameOriginOnly = options.sameOriginOnly ?? true;
  const maxRedirects = options.maxRedirects ?? DEFAULT_MAX_REDIRECTS;
  const includeSitemapCategories = options.includeSitemapCategories ?? false;
  const includeSitemapSummary = options.includeSitemapSummary ?? false;

  const baseParsed = new URL(sitemapUrl);
  const origin = baseParsed.origin;
  const seen = new Set<string>();
  const collected: string[] = [];
  let sitemapsFetched = 0;
  let rootCategories: SitemapIndexCategories | undefined;
  let rootType: SitemapRootType = 'unknown';
  const childSitemaps: SitemapChildSitemapResult[] = [];
  const childSitemapsByKey = new Map<string, SitemapChildSitemapResult>();
  // Pre-seeded with the root so a self-referencing index never re-fetches it.
  const seenChildKeys = new Set<string>([normalizeUrl(sitemapUrl)]);

  function isSameOrigin(href: string): boolean {
    try {
      return new URL(href).origin === origin;
    } catch {
      return false;
    }
  }

  function addUrl(url: string): void {
    const resolved = resolveUrl(url, sitemapUrl);
    const normalized = normalizeUrl(resolved);
    if (sameOriginOnly && !isSameOrigin(resolved)) return;
    if (seen.has(normalized)) return;
    if (collected.length >= maxUrls) return;
    seen.add(normalized);
    collected.push(resolved);
  }

  const queue: { url: string; key: string }[] = [
    { url: sitemapUrl, key: normalizeUrl(sitemapUrl) },
  ];

  while (queue.length > 0 && collected.length < maxUrls && sitemapsFetched < maxSitemaps) {
    const current = queue.shift()!;
    const isRoot = sitemapsFetched === 0;
    sitemapsFetched++;

    const xml = await fetchXml(current.url, {
      timeout,
      headers: options.headers,
      signal: options.signal,
      maxRedirects,
    });

    const base = current.url.replace(/\/[^/]*$/, '/');
    const parsed = parseSitemapXml(xml, base);

    if (isRoot) {
      rootType = parsed.type;
    }

    if (parsed.type === 'sitemapindex') {
      if (includeSitemapCategories && isRoot) {
        const categories: Record<string, SitemapCategoryInfo> = {};
        for (const u of parsed.urls) {
          const resolved = resolveUrl(u, base);
          const category = getSitemapCategoryFromUrl(resolved);
          if (!categories[category]) {
            categories[category] = { count: 0, urls: [] };
          }
          categories[category].count += 1;
          categories[category].urls.push(resolved);
        }
        rootCategories = { categories, totalSitemaps: parsed.urls.length };
      }
      for (const u of parsed.urls) {
        const resolved = resolveUrl(u, base);
        const key = normalizeUrl(resolved);
        // A child sitemap is only fetched and reported once, even if it appears
        // multiple times in one index or across several indexes.
        if (seenChildKeys.has(key)) continue;
        seenChildKeys.add(key);

        // A cross-origin child could never be fetched, so report it as such even
        // if the cap was also reached by the time we got to it.
        if (sameOriginOnly && !isSameOrigin(resolved)) {
          const child: SitemapChildSitemapResult = {
            url: resolved,
            status: 'cross-origin',
          };
          childSitemapsByKey.set(key, child);
          childSitemaps.push(child);
          continue;
        }

        // The original traversal stops queueing once this many sitemaps are
        // already fetched or queued. Keep that ordering and cap, and record why
        // each remaining child was left unfetched.
        if (sitemapsFetched + queue.length >= maxSitemaps) {
          const child: SitemapChildSitemapResult = {
            url: resolved,
            status: 'limit-exceeded',
            limit: 'maxSitemaps',
          };
          childSitemapsByKey.set(key, child);
          childSitemaps.push(child);
          continue;
        }

        const child: SitemapChildSitemapResult = {
          url: resolved,
          status: 'fetched',
        };
        childSitemapsByKey.set(key, child);
        childSitemaps.push(child);
        queue.push({ url: resolved, key });
      }
    } else {
      for (const u of parsed.urls) {
        addUrl(u);
        if (collected.length >= maxUrls) break;
      }
    }
  }

  // `maxUrls` is considered exhausted once we hold exactly that many URLs, even
  // if there happened to be nothing else to collect.
  const truncatedByMaxUrls =
    Number.isFinite(maxUrls) && collected.length >= maxUrls;

  // Anything still queued when the loop stopped was never fetched. Attribute it
  // to whichever cap ended the traversal; `maxUrls` is checked first because the
  // loop condition short-circuits on it.
  const remainingReason: 'maxUrls' | 'maxSitemaps' = truncatedByMaxUrls
    ? 'maxUrls'
    : 'maxSitemaps';
  for (const item of queue) {
    const child = childSitemapsByKey.get(item.key);
    if (!child) continue;
    child.status = 'limit-exceeded';
    child.limit = remainingReason;
  }

  // `maxSitemaps` truncated the traversal if a child was never queued because of
  // it. If work is still queued and `maxUrls` is not the cause, the sitemap cap
  // is the only other way the loop can have exited (`maxSitemaps: 0`).
  const truncatedByMaxSitemaps =
    (queue.length > 0 && !truncatedByMaxUrls) ||
    childSitemaps.some(
      (child) =>
        child.status === 'limit-exceeded' && child.limit === 'maxSitemaps',
    );

  function buildSummary(): SitemapFetchSummary {
    let childSitemapsFetched = 0;
    let childSitemapsSkippedCrossOrigin = 0;
    let childSitemapsSkippedLimit = 0;

    for (const child of childSitemaps) {
      if (child.status === 'fetched') childSitemapsFetched++;
      else if (child.status === 'cross-origin')
        childSitemapsSkippedCrossOrigin++;
      else childSitemapsSkippedLimit++;
    }

    return {
      rootType,
      hasChildSitemaps: childSitemaps.length > 0,
      childSitemapsFetched,
      sitemapsFetched,
      childSitemapsSkippedCrossOrigin,
      childSitemapsSkippedLimit,
      truncated: truncatedByMaxUrls || truncatedByMaxSitemaps,
      truncatedByMaxUrls,
      truncatedByMaxSitemaps,
      urlsCollected: collected.length,
      childSitemaps: childSitemaps.map((child) => ({ ...child })),
    };
  }

  if (includeSitemapSummary) {
    const summary = buildSummary();
    if (includeSitemapCategories && rootCategories) {
      return {
        urls: collected,
        sitemapCategories: rootCategories,
        sitemapSummary: summary,
      };
    }
    return { urls: collected, sitemapSummary: summary };
  }

  if (includeSitemapCategories && rootCategories) {
    return { urls: collected, sitemapCategories: rootCategories };
  }
  return collected;
}
