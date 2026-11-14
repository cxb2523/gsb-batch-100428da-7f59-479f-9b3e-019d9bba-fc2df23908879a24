/**
 * Types for the crawl module
 */
import type { FetchResult } from '../fetch/types.js';
import type { SelectorOptions } from '../extract/types.js';

export interface CrawlOptions extends SelectorOptions {
  maxDepth?: number;
  maxPages?: number;
  concurrency?: number;
  include?: string[];
  exclude?: string[];
  sameOrigin?: boolean;
  delay?: number;
  preset?: string;
  timeout?: number;
  proxy?: string;
  cookies?: Record<string, string>;
}

/**
 * How a single crawled page ended:
 * - `fetched`: a request was made (the fetch may still have failed — see `success`)
 * - `blocked`: robots.txt disallowed the URL, so no request was made
 * - `failed`: the fetch was attempted but did not produce content
 */
export type CrawlOutcome = 'fetched' | 'blocked' | 'failed';

export interface CrawlResult extends FetchResult {
  depth: number;
  /** Per-page disposition, present on every crawl result. */
  outcome: CrawlOutcome;
  /**
   * Human-readable explanation for non-successful pages
   * (robots.txt block reason or fetch failure reason).
   */
  reason?: string;
  /** The robots.txt pattern that blocked the page (only for `outcome: 'blocked'`). */
  blockedBy?: string;
}

export interface CrawlSummary {
  type: 'summary';
  pagesTotal: number;
  pagesSuccess: number;
  pagesFailed: number;
  pagesBlocked: number;
  durationMs: number;
  source: 'sitemap' | 'links';
  startUrl: string;
}
