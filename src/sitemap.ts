import { XMLParser, XMLValidator } from 'fast-xml-parser';

import { toErrorMessage } from './errors.js';
import { applyFilters } from './filters.js';
import { fetchPage } from './fetch.js';
import type { CrawlOptions, CrawlScope, SitemapDiscoveryRecord } from './types.js';

const SITEMAP_CANDIDATE_PATHS = ['/sitemap.xml', '/sitemap_index.xml'] as const;

export interface DiscoverSitemapOptions extends Pick<CrawlOptions, 'include' | 'exclude' | 'verbose'> {
  fetchDelayMs?: number;
  retryBackoffMs?: number;
  timeoutMs?: number;
  userAgent?: string;
}

export interface SitemapDiscovery {
  urls: string[] | null;
  records: SitemapDiscoveryRecord[];
}

interface UrlSetNode {
  url?: UrlNode | UrlNode[];
}

interface UrlNode {
  loc?: string;
}

interface SitemapIndexNode {
  sitemap?: SitemapNode | SitemapNode[];
}

interface SitemapNode {
  loc?: string;
}

interface SitemapDocument {
  urlset?: UrlSetNode;
  sitemapindex?: SitemapIndexNode;
}

const xmlParser = new XMLParser({
  ignoreAttributes: false,
  trimValues: true,
});

export async function discoverSitemap(
  seedUrl: string,
  scope: CrawlScope,
  options: DiscoverSitemapOptions,
): Promise<SitemapDiscovery> {
  const sitemapRoot = new URL(seedUrl).origin;
  const visitedSitemaps = new Set<string>();
  const records: SitemapDiscoveryRecord[] = [];

  for (const candidatePath of SITEMAP_CANDIDATE_PATHS) {
    const sitemapUrl = `${sitemapRoot}${candidatePath}`;
    const discoveredUrls = await collectSitemapUrls(sitemapUrl, scope, options, visitedSitemaps, records, false);

    if (discoveredUrls !== null && discoveredUrls.size > 0) {
      return { urls: [...discoveredUrls], records };
    }
  }

  return { urls: null, records };
}

async function collectSitemapUrls(
  sitemapUrl: string,
  scope: CrawlScope,
  options: DiscoverSitemapOptions,
  visitedSitemaps: Set<string>,
  records: SitemapDiscoveryRecord[],
  isNested: boolean,
): Promise<Set<string> | null> {
  if (visitedSitemaps.has(sitemapUrl)) {
    return new Set<string>();
  }

  visitedSitemaps.add(sitemapUrl);

  let document: SitemapDocument;
  try {
    const response = await fetchSitemapDocument(sitemapUrl, options);
    document = parseSitemap(response);
  } catch (error) {
    if (isNested) {
      records.push({
        url: sitemapUrl,
        status: 'failed',
        error: toErrorMessage(error),
        urlCount: 0,
      });
    }

    return null;
  }

  const urls = new Set<string>();

  for (const pageUrl of getUrlEntries(document)) {
    const decision = applyFilters(pageUrl, scope, options);
    if (decision.allowed) {
      urls.add(decision.normalizedUrl);
    }
  }

  if (isNested) {
    records.push({
      url: sitemapUrl,
      status: 'success',
      urlCount: urls.size,
    });
  }

  for (const nestedSitemapUrl of getNestedSitemapEntries(document)) {
    const nestedUrls = await collectSitemapUrls(nestedSitemapUrl, scope, options, visitedSitemaps, records, true);

    if (nestedUrls === null) {
      continue;
    }

    for (const discoveredUrl of nestedUrls) {
      urls.add(discoveredUrl);
    }
  }

  return urls;
}

async function fetchSitemapDocument(
  sitemapUrl: string,
  options: DiscoverSitemapOptions,
): Promise<string> {
  const fetchOptions: {
    delayMs?: number;
    retryBackoffMs?: number;
    timeoutMs?: number;
    userAgent?: string;
    verbose: boolean;
  } = {
    verbose: options.verbose,
  };

  if (options.fetchDelayMs !== undefined) {
    fetchOptions.delayMs = options.fetchDelayMs;
  }

  if (options.retryBackoffMs !== undefined) {
    fetchOptions.retryBackoffMs = options.retryBackoffMs;
  }

  if (options.timeoutMs !== undefined) {
    fetchOptions.timeoutMs = options.timeoutMs;
  }

  if (options.userAgent !== undefined) {
    fetchOptions.userAgent = options.userAgent;
  }

  const response = await fetchPage(sitemapUrl, fetchOptions);

  if (response.status >= 400) {
    throw new Error(`HTTP ${response.status}`);
  }

  return response.body ?? response.html ?? '';
}

function parseSitemap(xml: string): SitemapDocument {
  const validation = XMLValidator.validate(xml);
  if (validation !== true) {
    throw new Error(`Invalid sitemap XML: ${validation.err.msg}`);
  }

  return xmlParser.parse(xml) as SitemapDocument;
}

function getUrlEntries(document: SitemapDocument): string[] {
  return toArray(document.urlset?.url)
    .map((entry) => entry.loc?.trim())
    .filter((value): value is string => value !== undefined && value !== '');
}

function getNestedSitemapEntries(document: SitemapDocument): string[] {
  return toArray(document.sitemapindex?.sitemap)
    .map((entry) => entry.loc?.trim())
    .filter((value): value is string => value !== undefined && value !== '');
}

function toArray<T>(value: T | T[] | undefined): T[] {
  if (value === undefined) {
    return [];
  }

  return Array.isArray(value) ? value : [value];
}
