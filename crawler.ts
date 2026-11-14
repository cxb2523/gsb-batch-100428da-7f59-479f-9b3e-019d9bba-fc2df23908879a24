import { JSDOM } from "jsdom";
import { extractFast, extractDeep, type LinkFormat, type ImageMode } from "./extractor.js";
import { validateTargetUrl } from "./security.js";
import { fetchHtml } from "./extractor.js";

export interface DiscoveredUrl {
  url: string;
  source: "sitemap" | "robots" | "html_link";
  lastmod?: string;
}

export interface MapWebsiteOptions {
  url: string;
  maxLinks?: number;
  maxUrls?: number;
  includePatterns?: string[];
  excludePatterns?: string[];
  timeoutMs?: number;
}

/** What a fetched sitemap document claimed to be when parsed. */
export type SitemapKind = "index" | "urlset" | "unknown";

/** Final outcome of trying to read one sitemap address. */
export type SitemapStatus = "ok" | "http_error" | "parse_error" | "skipped_limit";

export interface SitemapOutcome {
  url: string;
  kind: SitemapKind;
  status: SitemapStatus;
  /** In-scope page URLs this document added; an index contributes 0 directly. */
  pagesFound: number;
  error?: string;
}

export interface MapWebsiteResult {
  host: string;
  /** Sitemap addresses declared by robots.txt (or the default fallback). */
  sitemapsFound: string[];
  totalDiscovered: number;
  urls: DiscoveredUrl[];
  /** One entry per sitemap read attempt, including index children and maps left unread. */
  sitemapOutcomes: SitemapOutcome[];
}

export interface CrawlWebsiteOptions {
  startUrl?: string;
  url?: string;
  maxDepth?: number;
  maxPages?: number;
  concurrency?: number;
  includePatterns?: string[];
  excludePatterns?: string[];
  mode?: "fast" | "deep";
  linkFormat?: LinkFormat;
  imageMode?: ImageMode;
  headers?: Record<string, string>;
  cookies?: Record<string, string>;
  proxy?: string;
  timeoutMs?: number;
}

export interface CrawledPage {
  url: string;
  depth: number;
  markdown: string;
  charCount: number;
  estimatedTokens: number;
  error?: string;
}

export interface CrawlWebsiteResult {
  startUrl: string;
  totalPagesCrawled: number;
  totalEstimatedTokens: number;
  pages: CrawledPage[];
}

/**
 * Classify a sitemap document. Only the root element decides: a `<sitemapindex>`
 * lists more maps, a `<urlset>` lists pages, anything else is `unknown`.
 */
export function classifySitemapDocument(xml: string): SitemapKind {
  const body = xml
    .replace(/^﻿/, "")
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<\?xml[\s\S]*?\?>/i, "")
    .replace(/<!DOCTYPE[\s\S]*?>/i, "");
  // Only the root element decides; a stray `<sitemapindex` elsewhere in the
  // body (an HTML error page, a CDATA sample) must not reclassify the document.
  const root = body.match(/^\s*<([A-Za-z_][\w:.-]*)/)?.[1]?.toLowerCase() || "";
  const localName = root.includes(":") ? root.slice(root.lastIndexOf(":") + 1) : root;
  if (localName === "sitemapindex") return "index";
  if (localName === "urlset") return "urlset";
  return "unknown";
}

/** Pull the `<loc>` values out of a sitemap document, with any nearby `lastmod`. */
function parseSitemapLocs(xml: string): Array<{ loc: string; lastmod?: string }> {
  const locRegex = /<loc>\s*(https?:\/\/[^<\s]+)\s*<\/loc>/gi;
  const lastmodRegex = /<lastmod>\s*([^<\s]+)\s*<\/lastmod>/i;
  const entries: Array<{ loc: string; lastmod?: string }> = [];
  let locMatch: RegExpExecArray | null;
  while ((locMatch = locRegex.exec(xml)) !== null) {
    // Extract snippet for lastmod if present around loc
    const chunk = xml.slice(locMatch.index, locMatch.index + 300);
    const lastmodMatch = chunk.match(lastmodRegex);
    entries.push({ loc: locMatch[1].trim(), lastmod: lastmodMatch ? lastmodMatch[1] : undefined });
  }
  return entries;
}

/**
 * How far sitemap indexes may nest before we stop following them. The top-level
 * sitemap is depth 0, so a child at `depth > MAX_SITEMAP_DEPTH` is refused and
 * recorded as a parse error. This is what stops a self-referencing index from
 * expanding forever.
 */
export const MAX_SITEMAP_DEPTH = 3;

/**
 * Hard cap on sitemap documents read per `mapWebsite` call, so a wide (or
 * adversarial) index cannot turn one map request into unbounded traffic.
 */
export const MAX_SITEMAP_DOCUMENTS = 200;

const MAX_URL_PATTERNS = 10;
const MAX_URL_PATTERN_LENGTH = 200;

/**
 * Compile include/exclude filters. Patterns are case-sensitive substrings where
 * `*` matches any run of characters; they are deliberately NOT regexes because
 * crawled URLs are attacker-controlled and a backtracking regex like
 * `^(([a-z/:.])+)+$` blocks the event loop for seconds (ReDoS).
 */
export function compileUrlPatterns(patterns: string[], kind: "include" | "exclude"): Array<(s: string) => boolean> {
  if (patterns.length > MAX_URL_PATTERNS) throw new Error(`Too many ${kind} patterns (max ${MAX_URL_PATTERNS})`);
  return patterns.map((p) => {
    if (p.length > MAX_URL_PATTERN_LENGTH) throw new Error(`${kind} pattern is too long (max ${MAX_URL_PATTERN_LENGTH} characters)`);
    // Fail loudly on old regex syntax: silently matching it literally would turn
    // an exclude like "logout|admin" into a no-op. ("?" and "+" stay legal: URLs use them.)
    if (/[\\^$()[\]{}|]|\.\*/.test(p)) {
      throw new Error(`Invalid ${kind} pattern "${p}": patterns are substrings where * is the only wildcard, not regexes`);
    }
    const parts = p.split("*").filter(Boolean);
    return (s: string) => {
      let at = 0;
      for (const part of parts) {
        const i = s.indexOf(part, at);
        if (i < 0) return false;
        at = i + part.length;
      }
      return true;
    };
  });
}

function makeUrlFilter(host: string, includePatterns: string[], excludePatterns: string[]): (candidate: string) => boolean {
  const include = compileUrlPatterns(includePatterns, "include");
  const exclude = compileUrlPatterns(excludePatterns, "exclude");
  return (candidate) => {
    try {
      const parsed = new URL(candidate);
      if (parsed.hostname !== host) return false;
      const hit = (m: (s: string) => boolean) => m(parsed.pathname) || m(candidate);
      if (exclude.some(hit)) return false;
      return include.length === 0 || include.some(hit);
    } catch {
      return false;
    }
  };
}

/**
 * Discover the architecture and page inventory of a website via sitemaps, robots.txt, and link graphs.
 */
export async function mapWebsite(options: MapWebsiteOptions): Promise<MapWebsiteResult> {
  const { url, timeoutMs = 10000, includePatterns = [], excludePatterns = [] } = options;
  const maxLimit = options.maxUrls ?? options.maxLinks ?? 100;
  await validateTargetUrl(url);

  const parsedStart = new URL(url);
  const origin = parsedStart.origin;
  const sitemapsFound: string[] = [];
  const discoveredMap = new Map<string, DiscoveredUrl>();

  const isUrlAllowed = makeUrlFilter(parsedStart.hostname, includePatterns, excludePatterns);

  // 1. Inspect robots.txt for declared sitemaps
  try {
    const robotsUrl = `${origin}/robots.txt`;
    const robotsTxt = await fetchHtml(robotsUrl, { timeoutMs: 5000 });
    const sitemapRegex = /Sitemap:\s*(https?:\/\/[^\s\r\n]+)/gi;
    let match: RegExpExecArray | null;
    while ((match = sitemapRegex.exec(robotsTxt)) !== null) {
      const sitemapUrl = match[1].trim();
      if (!sitemapsFound.includes(sitemapUrl)) {
        sitemapsFound.push(sitemapUrl);
      }
    }
  } catch {
    /* robots.txt unavailable or blocked */
  }

  // Fallback to default sitemap.xml if none detected
  if (sitemapsFound.length === 0) {
    sitemapsFound.push(`${origin}/sitemap.xml`);
  }

  // 2. Fetch and parse sitemaps. A sitemap may be an index whose entries are
  // more sitemaps, so this is a worklist rather than a flat loop. Each address
  // leaves exactly one outcome behind, whether it was read, failed, or never
  // got a turn.
  const sitemapOutcomes: SitemapOutcome[] = [];
  const sitemapQueue: Array<{ url: string; depth: number }> = sitemapsFound.map((sitemapUrl) => ({
    url: sitemapUrl,
    depth: 0,
  }));
  // Ordinary children are fetched once even when several indexes list them.
  // A child that points back at the document listing it is deliberately NOT
  // deduped: it walks down until the depth cap records the parse_error.
  const visitedSitemaps = new Set(sitemapsFound);
  let sitemapsRead = 0;

  while (sitemapQueue.length > 0) {
    const { url: sitemapUrl, depth } = sitemapQueue.shift()!;

    if (sitemapsRead >= MAX_SITEMAP_DOCUMENTS) {
      sitemapOutcomes.push({
        url: sitemapUrl,
        kind: "unknown",
        status: "skipped_limit",
        pagesFound: 0,
        error: `Sitemap document limit (${MAX_SITEMAP_DOCUMENTS}) reached before this sitemap was read`,
      });
      continue;
    }
    if (discoveredMap.size >= maxLimit) {
      sitemapOutcomes.push({
        url: sitemapUrl,
        kind: "unknown",
        status: "skipped_limit",
        pagesFound: 0,
        error: `maxUrls limit (${maxLimit}) reached before this sitemap was read`,
      });
      continue;
    }
    if (depth > MAX_SITEMAP_DEPTH) {
      sitemapOutcomes.push({
        url: sitemapUrl,
        kind: "unknown",
        status: "parse_error",
        pagesFound: 0,
        error: `Sitemap nesting depth ${depth} exceeds the limit of ${MAX_SITEMAP_DEPTH}`,
      });
      continue;
    }

    const outcome: SitemapOutcome = { url: sitemapUrl, kind: "unknown", status: "ok", pagesFound: 0 };
    sitemapOutcomes.push(outcome);

    let xmlContent: string;
    try {
      sitemapsRead++;
      xmlContent = await fetchHtml(sitemapUrl, { timeoutMs });
    } catch (error: any) {
      outcome.status = "http_error";
      outcome.error = error?.message || String(error);
      continue; // one bad sub-map must not abort the rest
    }

    const kind = classifySitemapDocument(xmlContent);
    outcome.kind = kind;

    if (kind === "unknown") {
      outcome.status = "parse_error";
      outcome.error = "Document root is neither <urlset> nor <sitemapindex>";
      continue;
    }

    const entries = parseSitemapLocs(xmlContent);

    if (kind === "index") {
      // Child sitemaps are not page URLs, so the include/exclude and same-host
      // filters do not apply to them; validateTargetUrl inside fetchHtml still
      // guards the request itself.
      for (const { loc } of entries) {
        // An index that lists itself must stay queued, otherwise the depth
        // cap would never see it and the cycle would go unreported.
        const isSelfReference = loc === sitemapUrl;
        if (!isSelfReference && visitedSitemaps.has(loc)) continue;
        visitedSitemaps.add(loc);
        sitemapQueue.push({ url: loc, depth: depth + 1 });
      }
      continue;
    }

    for (const { loc, lastmod } of entries) {
      if (discoveredMap.size >= maxLimit) break;
      if (isUrlAllowed(loc) && !discoveredMap.has(loc)) {
        discoveredMap.set(loc, { url: loc, source: "sitemap", lastmod });
        outcome.pagesFound++;
      }
    }
  }

  // 3. If sitemaps yielded few links, parse links from root HTML page
  if (discoveredMap.size < 10) {
    try {
      const homeHtml = await fetchHtml(url, { timeoutMs });
      const dom = new JSDOM(homeHtml, { url });
      const anchors = Array.from(dom.window.document.querySelectorAll("a[href]"));

      for (const a of anchors) {
        if (discoveredMap.size >= maxLimit) break;
        const href = a.getAttribute("href");
        if (!href) continue;

        try {
          const resolved = new URL(href, url);
          if (resolved.protocol === "http:" || resolved.protocol === "https:") {
            resolved.hash = "";
            const cleanUrl = resolved.toString();
            if (isUrlAllowed(cleanUrl) && !discoveredMap.has(cleanUrl)) {
              discoveredMap.set(cleanUrl, {
                url: cleanUrl,
                source: "html_link",
              });
            }
          }
        } catch {
          /* ignore unparseable links */
        }
      }
    } catch {
      /* home page fetch failed */
    }
  }

  const urls = Array.from(discoveredMap.values()).slice(0, maxLimit);

  return {
    host: parsedStart.hostname,
    sitemapsFound,
    totalDiscovered: urls.length,
    urls,
    sitemapOutcomes,
  };
}

/**
 * Pull every outbound href out of an already-extracted markdown document.
 *
 * Covers both link formats we emit: inline `[text](url)` and the footnote
 * reference block (`[1]: https://...`) produced by `link_format: references`.
 * `strip` yields neither, which is why the caller falls back to a refetch.
 */
export function extractHrefsFromMarkdown(markdown: string): string[] {
  const hrefs: string[] = [];
  const inline = /\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)/g;
  let m: RegExpExecArray | null;
  while ((m = inline.exec(markdown)) !== null) {
    hrefs.push(m[1]);
  }
  const footnote = /^\[\d+\]:\s*(\S+)/gm;
  while ((m = footnote.exec(markdown)) !== null) {
    hrefs.push(m[1]);
  }
  return hrefs;
}

/**
 * Perform autonomous, recursive website crawling with depth boundaries, regex route filtering, and token accounting.
 */
export async function crawlWebsite(options: CrawlWebsiteOptions): Promise<CrawlWebsiteResult> {
  const startUrl = options.startUrl || options.url;
  if (!startUrl) {
    throw new Error("Missing startUrl for crawler");
  }
  const {
    maxDepth = 2,
    maxPages = 10,
    concurrency = 3,
    includePatterns = [],
    excludePatterns = [],
    mode = "fast",
    linkFormat,
    imageMode,
    headers,
    cookies,
    proxy,
    timeoutMs = 15000,
  } = options;

  await validateTargetUrl(startUrl);
  const targetHost = new URL(startUrl).hostname;

  const isUrlAllowed = makeUrlFilter(targetHost, includePatterns, excludePatterns);

  const queue: Array<{ url: string; depth: number }> = [{ url: startUrl, depth: 0 }];
  const visited = new Set<string>([startUrl]);
  const pages: CrawledPage[] = [];

  const crawlPage = async ({ url: currentUrl, depth }: { url: string; depth: number }): Promise<void> => {
    try {
      let markdown: string;
      if (mode === "deep") {
        markdown = await extractDeep({
          url: currentUrl,
          timeoutMs,
          headers,
          cookies,
          proxy,
          linkFormat,
          imageMode,
        });
      } else {
        markdown = await extractFast({
          url: currentUrl,
          timeoutMs,
          headers,
          cookies,
          proxy,
          linkFormat,
          imageMode,
        });
      }

      const charCount = markdown.length;
      const estimatedTokens = Math.ceil(charCount / 4);

      pages.push({
        url: currentUrl,
        depth,
        markdown,
        charCount,
        estimatedTokens,
      });

      // If depth allows, discover outgoing links from this page
      if (depth < maxDepth && pages.length + queue.length < maxPages * 2) {
        try {
          // Reuse the markdown we already have instead of re-fetching the
          // page. A second fetchHtml doubles the traffic and, in `deep`
          // mode, returns raw SPA markup with an empty <div id="root">, so
          // link discovery silently found nothing and the crawl stopped.
          // The rendered markdown carries every href the browser saw.
          let hrefs = extractHrefsFromMarkdown(markdown);
          if (hrefs.length === 0 && linkFormat === "strip") {
            // `strip` removes URLs by design; the page may still be worth
            // one extra fetch if it has no other way to expose its links.
            const html = await fetchHtml(currentUrl, { timeoutMs: 5000, headers, cookies, proxy });
            const dom = new JSDOM(html, { url: currentUrl });
            hrefs = Array.from(dom.window.document.querySelectorAll("a[href]"))
              .map((a) => a.getAttribute("href") || "");
          }

          for (const href of hrefs) {
            if (!href) continue;
            try {
              const resolved = new URL(href, currentUrl);
              resolved.hash = "";
              const normalizedUrl = resolved.toString();

              if (!visited.has(normalizedUrl) && isUrlAllowed(normalizedUrl)) {
                visited.add(normalizedUrl);
                queue.push({ url: normalizedUrl, depth: depth + 1 });
              }
            } catch {
              /* skip */
            }
          }
        } catch {
          /* link discovery error ignored */
        }
      }
    } catch (err: any) {
      pages.push({
        url: currentUrl,
        depth,
        markdown: "",
        charCount: 0,
        estimatedTokens: 0,
        error: err.message || String(err),
      });
    }
  };

  // Worker pool: a slot is refilled as soon as its page finishes. Fixed-size
  // waves (`Promise.all` per batch) left every slot idle until the slowest page
  // of the batch returned.
  let inFlight = 0;
  await new Promise<void>((resolve) => {
    const pump = () => {
      while (inFlight < concurrency && queue.length > 0 && pages.length + inFlight < maxPages) {
        const item = queue.shift()!;
        inFlight++;
        crawlPage(item).finally(() => {
          inFlight--;
          pump();
        });
      }
      if (inFlight === 0) resolve();
    };
    pump();
  });

  const totalEstimatedTokens = pages.reduce((sum, p) => sum + p.estimatedTokens, 0);

  return {
    startUrl,
    totalPagesCrawled: pages.length,
    totalEstimatedTokens,
    pages,
  };
}
