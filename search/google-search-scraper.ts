/**
 * Google Search Results Scraper
 *
 * Extract organic search results and the featured snippet from Google Search,
 * page by page, with an explicit outcome recorded for every page.
 *
 * Usage:
 *   SPIDER_API_KEY=sk-... npx tsx search/google-search-scraper.ts
 *   SPIDER_API_KEY=sk-... npx tsx search/google-search-scraper.ts "web scraping api" 3
 *   SPIDER_API_KEY=sk-... npx tsx search/google-search-scraper.ts --query "web scraping api" --pages 3
 *
 * Defaults match the original single-page script: query "web scraping api", 1 page.
 *
 * Output (stdout) is one JSON summary:
 *   - `results`: merged, de-duplicated results in page order. The first
 *     occurrence of a URL wins and `position` is renumbered 1..N after merging.
 *   - `pageOutcomes`: one entry per requested page with `page`, `outcome`
 *     ("ok" | "empty" | "blocked" | "error"), `resultCount`, and `reason`
 *     when something went wrong.
 *
 * Exit codes:
 *   0  at least one page produced results or a genuine empty result page
 *   1  every page was blocked or errored (the summary is still printed)
 *   2  invalid command-line arguments
 *
 * Local verification (no Google, no API key needed):
 *   npm run test:google-search
 * The tests drive the real pagination/merge/classification code with synthetic
 * HTML fixtures under search/__tests__/fixtures. Stub pages are test doubles
 * only; they are never presented as live Google results.
 */

import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { SpiderBrowser } from "spider-browser";

export const DEFAULT_QUERY = "web scraping api";
export const DEFAULT_PAGES = 1;
export const RESULTS_PER_PAGE = 10;
export const DEFAULT_PAGE_DELAY_MS = 1000;
export const MAX_PAGES = 100;

/** How a single page ended. */
export type PageOutcomeType = "ok" | "empty" | "blocked" | "error";

export interface SerpResultItem {
  title: string;
  url: string;
  snippet: string | null;
}

/** Signals extracted from one rendered Google result page. */
export interface RawSerpPayload {
  title: string;
  url: string;
  hasRso: boolean;
  rsoItems: number;
  results: SerpResultItem[];
  featured: string | null;
  /**
   * Captcha/blocked markers. These mean the page never rendered results,
   * so they always win over anything else found on it.
   */
  hardBlockers: string[];
  /**
   * Consent/interstitial markers. A consent wall can sit above a page that still
   * rendered results, so these only count as a block when no results were found.
   */
  softBlockers: string[];
  noResultsText: boolean;
}

/** A result after cross-page merging. */
export interface MergedSerpResult extends SerpResultItem {
  /** 1-based position in the merged list. */
  position: number;
  /** 1-based page the result was first seen on. */
  page: number;
}

export interface PageOutcome {
  /** 1-based page number. */
  page: number;
  outcome: PageOutcomeType;
  /**
   * Results this page contributed to the merge, before cross-page de-duplication.
   * Always 0 for "blocked", "empty", and "error" pages.
   */
  resultCount: number;
  /** Explanation for "blocked" and "error" (and some "empty") outcomes. */
  reason?: string;
}

export type SummaryStatus = "ok" | "no_results" | "blocked_or_error";

export interface SerpSummary {
  query: string;
  pagesRequested: number;
  status: SummaryStatus;
  featured: string | null;
  results: MergedSerpResult[];
  pageOutcomes: PageOutcome[];
}

/**
 * Minimal page surface needed by the scraper. `SpiderPage` satisfies it
 * structurally; tests provide an in-memory implementation.
 */
export interface SerpPage {
  goto(url: string): Promise<void>;
  content(waitMs?: number, minLength?: number): Promise<string>;
  evaluate(expression: string): Promise<unknown>;
}

/**
 * In-page extraction script. Runs inside the browser via `page.evaluate()`
 * and returns a JSON string so the payload survives both CDP and BiDi.
 *
 * The selectors are the ones used by the original script:
 * `#rso .g`, `h3`, `a[href]`, the `div[data-sncf]` / line-clamp snippet
 * wrapper, and the `[data-attrid*=description]` featured snippet.
 */
export const SERP_EXTRACT_SCRIPT = `(() => {
  const results = [];
  const items = Array.from(document.querySelectorAll("#rso .g"));
  items.forEach((item) => {
    const title = item.querySelector("h3")?.textContent?.trim() || "";
    const url = item.querySelector("a[href]")?.getAttribute("href")?.trim() || "";
    const snippet = item.querySelector("div[data-sncf] span, div[style*='-webkit-line-clamp'] span")?.textContent?.trim()
      || item.querySelector("div[data-sncf]")?.textContent?.trim()
      || null;
    if (title && url.indexOf("http") === 0) {
      results.push({ title: title, url: url, snippet: snippet });
    }
  });

  const featured = document.querySelector("[data-attrid='wa:/description'] span")?.textContent?.trim()
    || document.querySelector("[data-attrid*='description'] span")?.textContent?.trim()
    || null;

  const href = typeof location !== "undefined" && location.href ? location.href : "";
  const pageTitle = document.title || "";
  const bodyText = document.body && document.body.textContent ? document.body.textContent : "";
  const lower = bodyText.toLowerCase();

  // Hard blockers: captcha / "unusual traffic" pages never carry organic results.
  const hardBlockers = [];
  if (href.indexOf("/sorry/") !== -1) hardBlockers.push("url:/sorry/");

  const hardSelectors = [
    "#captcha-form",
    "form[action*='sorry']",
    "iframe[src*='recaptcha']",
    ".g-recaptcha",
    "#recaptcha-anchor",
    "#px-captcha"
  ];
  hardSelectors.forEach((selector) => {
    if (document.querySelector(selector)) hardBlockers.push("selector:" + selector);
  });

  const hardPhrases = [
    "our systems have detected unusual traffic",
    "this page checks to see if it is really you",
    "enable javascript and cookies to continue"
  ];
  hardPhrases.forEach((phrase) => {
    if (lower.indexOf(phrase) !== -1) hardBlockers.push("text:" + phrase);
  });

  // Soft blockers: consent walls, which may overlay an otherwise usable SERP.
  const softBlockers = [];
  if (href.indexOf("consent.google.") !== -1) softBlockers.push("url:consent.google");
  const softSelectors = ["form[action*='consent.google.com']", "#consent-bump"];
  softSelectors.forEach((selector) => {
    if (document.querySelector(selector)) softBlockers.push("selector:" + selector);
  });
  if (lower.indexOf("before you continue to google") !== -1) {
    softBlockers.push("text:before you continue to google");
  }

  return JSON.stringify({
    title: pageTitle.slice(0, 300),
    url: href,
    hasRso: !!document.querySelector("#rso"),
    rsoItems: items.length,
    results: results.slice(0, ${RESULTS_PER_PAGE}),
    featured: featured,
    hardBlockers: Array.from(new Set(hardBlockers)),
    softBlockers: Array.from(new Set(softBlockers)),
    noResultsText: lower.indexOf("did not match any documents") !== -1
      || lower.indexOf("no results found for") !== -1
  });
})()`;

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asBoolean(value: unknown): boolean {
  return value === true;
}

function asCount(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
}

function normalizeResultItem(value: unknown): SerpResultItem | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const title = asString(record.title).trim();
  const url = asString(record.url).trim();
  if (!title || !url.startsWith("http")) return null;
  const snippetValue = record.snippet;
  const snippet = typeof snippetValue === "string" && snippetValue.trim() ? snippetValue.trim() : null;
  return { title, url, snippet };
}

function collectSignals(record: Record<string, unknown>, key: string): string[] {
  const raw = Array.isArray(record[key]) ? (record[key] as unknown[]) : [];
  const signals: string[] = [];
  for (const entry of raw) {
    const text = asString(entry).trim();
    if (text && !signals.includes(text)) signals.push(text);
  }
  return signals.slice(0, 20);
}

/** Validate and normalize the JSON payload returned by the in-page script. */
export function parseRawPayload(raw: unknown): RawSerpPayload {
  if (typeof raw !== "string") {
    throw new Error(`evaluate() returned ${raw === null ? "null" : typeof raw}; expected a JSON string`);
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    throw new Error(`evaluate() returned invalid JSON: ${errorMessage(error)}`);
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("evaluate() payload is not an object");
  }

  const record = parsed as Record<string, unknown>;
  const rawResults = Array.isArray(record.results) ? record.results : [];
  const results: SerpResultItem[] = [];
  for (const item of rawResults) {
    const normalized = normalizeResultItem(item);
    if (normalized) results.push(normalized);
  }

  const featuredText = asString(record.featured).trim();

  return {
    title: asString(record.title),
    url: asString(record.url),
    hasRso: asBoolean(record.hasRso),
    rsoItems: asCount(record.rsoItems),
    results,
    featured: featuredText || null,
    hardBlockers: collectSignals(record, "hardBlockers"),
    softBlockers: collectSignals(record, "softBlockers"),
    noResultsText: asBoolean(record.noResultsText),
  };
}

export interface PageClassification {
  outcome: PageOutcomeType;
  resultCount: number;
  reason?: string;
}

/**
 * Decide the outcome of one page from its extracted signals.
 *
 * Hard captcha markers always mean "blocked": such a page never rendered
 * results. An extraction that yielded results is "ok" (a consent banner may
 * still be present, but the results themselves are usable). Otherwise a soft
 * consent marker means "blocked"; a present-but-unusable result area or
 * Google's explicit "did not match any documents" page is "empty"; anything
 * else is an "error", because an unrecognized layout cannot be claimed to mean
 * the query really has no results.
 */
export function classifySerpPayload(payload: RawSerpPayload): PageClassification {
  const resultCount = payload.results.length;

  if (payload.hardBlockers.length > 0) {
    return {
      outcome: "blocked",
      resultCount: 0,
      reason: `captcha or block page: ${payload.hardBlockers.join(", ")}`,
    };
  }

  if (resultCount > 0) {
    return { outcome: "ok", resultCount };
  }

  if (payload.softBlockers.length > 0) {
    return {
      outcome: "blocked",
      resultCount: 0,
      reason: `consent/interstitial page: ${payload.softBlockers.join(", ")}`,
    };
  }

  if (payload.hasRso) {
    return {
      outcome: "empty",
      resultCount: 0,
      reason:
        payload.rsoItems > 0
          ? `result container #rso had ${payload.rsoItems} item(s) but none were extractable`
          : "result container #rso was present but empty",
    };
  }

  if (payload.noResultsText) {
    return { outcome: "empty", resultCount: 0, reason: "Google page reported no matching documents" };
  }

  return {
    outcome: "error",
    resultCount: 0,
    reason: "result container #rso not found (unrecognized page layout)",
  };
}

export function buildGoogleSerpUrl(query: string, pageNumber: number): string {
  const encodedQuery = encodeURIComponent(query.trim());
  const start = (pageNumber - 1) * RESULTS_PER_PAGE;
  const base = `https://www.google.com/search?q=${encodedQuery}`;
  return start > 0 ? `${base}&start=${start}` : base;
}

export interface ScrapeGoogleSerpOptions {
  query: string;
  pages: number;
  /** Delay between page navigations (default 1000 ms; tests pass 0). */
  pageDelayMs?: number;
  sleep?: (ms: number) => Promise<void>;
  /** Called synchronously after each page is classified. */
  onPageOutcome?: (outcome: PageOutcome) => void;
}

/**
 * Visit `pages` Google result pages and merge their results.
 *
 * A page that is blocked, fails to navigate, or fails to parse never aborts
 * the run: it is recorded in `pageOutcomes` and the loop continues.
 */
export async function scrapeGoogleSerp(
  page: SerpPage,
  options: ScrapeGoogleSerpOptions
): Promise<SerpSummary> {
  const pages = options.pages;
  const pageDelayMs = options.pageDelayMs ?? DEFAULT_PAGE_DELAY_MS;
  const sleep = options.sleep ?? defaultSleep;
  const pageOutcomes: PageOutcome[] = [];
  const merged: MergedSerpResult[] = [];
  const seenUrls = new Set<string>();
  let featured: string | null = null;

  for (let pageNumber = 1; pageNumber <= pages; pageNumber += 1) {
    if (pageNumber > 1 && pageDelayMs > 0) {
      await sleep(pageDelayMs);
    }

    const url = buildGoogleSerpUrl(options.query, pageNumber);
    let payload: RawSerpPayload;

    try {
      await page.goto(url);
      // content() waits for the SERP DOM to settle before extraction, matching
      // the readiness call the original script made after goto().
      await page.content();
      payload = parseRawPayload(await page.evaluate(SERP_EXTRACT_SCRIPT));
    } catch (error) {
      const outcome: PageOutcome = {
        page: pageNumber,
        outcome: "error",
        resultCount: 0,
        reason: `navigation or extraction failed: ${errorMessage(error)}`,
      };
      pageOutcomes.push(outcome);
      options.onPageOutcome?.(outcome);
      continue;
    }

    const classification = classifySerpPayload(payload);
    const outcome: PageOutcome = {
      page: pageNumber,
      outcome: classification.outcome,
      resultCount: classification.resultCount,
    };
    if (classification.reason) outcome.reason = classification.reason;
    pageOutcomes.push(outcome);
    options.onPageOutcome?.(outcome);

    // Results from blocked/error pages are not trusted or merged.
    if (classification.outcome !== "ok") continue;

    if (!featured && payload.featured) featured = payload.featured;

    for (const item of payload.results) {
      if (seenUrls.has(item.url)) continue;
      seenUrls.add(item.url);
      merged.push({
        position: merged.length + 1,
        title: item.title,
        url: item.url,
        snippet: item.snippet,
        page: pageNumber,
      });
    }
  }

  return {
    query: options.query,
    pagesRequested: pages,
    status: summarizeStatus(pageOutcomes, merged.length),
    featured,
    results: merged,
    pageOutcomes,
  };
}

export function summarizeStatus(pageOutcomes: PageOutcome[], resultCount: number): SummaryStatus {
  const allFailed =
    pageOutcomes.length > 0 &&
    pageOutcomes.every((outcome) => outcome.outcome === "blocked" || outcome.outcome === "error");
  if (allFailed) return "blocked_or_error";
  return resultCount > 0 ? "ok" : "no_results";
}

/**
 * 0 when at least one page gave a usable answer (results or a genuine empty
 * result page), 1 when every page was blocked or errored.
 */
export function exitCodeForSummary(summary: SerpSummary): number {
  return summary.pageOutcomes.some(
    (outcome) => outcome.outcome === "ok" || outcome.outcome === "empty"
  )
    ? 0
    : 1;
}

export function failureSummary(query: string, pages: number, reason: string): SerpSummary {
  const pageOutcomes: PageOutcome[] = Array.from({ length: pages }, (_, index) => ({
    page: index + 1,
    outcome: "error" as const,
    resultCount: 0,
    reason,
  }));
  return {
    query,
    pagesRequested: pages,
    status: "blocked_or_error",
    featured: null,
    results: [],
    pageOutcomes,
  };
}

export class CliUsageError extends Error {}

export interface CliOptions {
  query: string;
  pages: number;
  help?: boolean;
}

function requireValue(argv: string[], index: number, flag: string): string {
  const value = argv[index];
  if (value === undefined || value.startsWith("-")) {
    throw new CliUsageError(`${flag} requires a value`);
  }
  return value;
}

function parsePageCount(value: string): number {
  const pages = Number(value);
  if (!Number.isInteger(pages) || pages < 1 || pages > MAX_PAGES) {
    throw new CliUsageError(`page count must be an integer between 1 and ${MAX_PAGES}, got "${value}"`);
  }
  return pages;
}

/**
 * Parse CLI arguments.
 *
 * Supported forms (flags win over positionals):
 *   [query] [pages]
 *   --query <text> / -q <text> / --query=<text>
 *   --pages <n> / -p <n> / --pages=<n>
 */
export function parseCliArgs(argv: string[]): CliOptions {
  let query: string | null = null;
  let pages: number | null = null;
  const positional: string[] = [];

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]!;

    if (arg === "--help" || arg === "-h") {
      return { query: DEFAULT_QUERY, pages: DEFAULT_PAGES, help: true };
    }
    if (arg === "--query" || arg === "-q") {
      query = requireValue(argv, index + 1, arg);
      index += 1;
    } else if (arg.startsWith("--query=")) {
      query = arg.slice("--query=".length);
    } else if (arg === "--pages" || arg === "-p") {
      pages = parsePageCount(requireValue(argv, index + 1, arg));
      index += 1;
    } else if (arg.startsWith("--pages=")) {
      pages = parsePageCount(arg.slice("--pages=".length));
    } else if (arg.startsWith("-")) {
      throw new CliUsageError(`unknown option: ${arg}`);
    } else {
      positional.push(arg);
    }
  }

  if (positional.length > 2) {
    throw new CliUsageError(`unexpected argument: ${positional[2]}`);
  }
  if (query === null && positional.length > 0) query = positional[0]!;
  if (pages === null && positional.length > 1) pages = parsePageCount(positional[1]!);

  const finalQuery = (query ?? DEFAULT_QUERY).trim();
  if (!finalQuery) {
    throw new CliUsageError("query must not be empty");
  }

  return { query: finalQuery, pages: pages ?? DEFAULT_PAGES };
}

export const USAGE = `Usage:
  SPIDER_API_KEY=sk-... npx tsx search/google-search-scraper.ts [query] [pages]
  SPIDER_API_KEY=sk-... npx tsx search/google-search-scraper.ts --query <text> --pages <n>

Options:
  -q, --query <text>   Search query (default: "${DEFAULT_QUERY}")
  -p, --pages <n>      Number of result pages to collect, 1-${MAX_PAGES} (default: ${DEFAULT_PAGES})
  -h, --help           Show this help

Prints a JSON summary to stdout and per-page progress to stderr.
Exit code 0: at least one page was usable; 1: every page was blocked/errored;
2: invalid arguments.`;

function defaultSleep(ms: number): Promise<void> {
  return new Promise((done) => setTimeout(done, ms));
}

function errorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message.length > 300 ? `${message.slice(0, 300)}...` : message;
}

export interface BrowserSession {
  page: SerpPage;
  close(): Promise<void>;
}

export interface CliDependencies {
  /** Injected for tests; defaults to a real SpiderBrowser session. */
  openBrowser?: () => Promise<BrowserSession>;
  writeOut?: (text: string) => void;
  writeErr?: (text: string) => void;
  sleep?: (ms: number) => Promise<void>;
}

async function openSpiderBrowser(): Promise<BrowserSession> {
  const apiKey = process.env.SPIDER_API_KEY;
  if (!apiKey) {
    throw new Error("SPIDER_API_KEY is not set");
  }
  const spider = new SpiderBrowser({ apiKey, stealth: 2 });
  try {
    await spider.init();
  } catch (error) {
    await spider.close().catch(() => undefined);
    throw error;
  }
  return { page: spider.page, close: () => spider.close() };
}

function formatOutcome(outcome: PageOutcome): string {
  const suffix = outcome.reason ? ` - ${outcome.reason}` : "";
  return `page ${outcome.page}: ${outcome.outcome} (${outcome.resultCount} result(s))${suffix}\n`;
}

/** Run the CLI and return the intended process exit code. */
export async function runCli(argv: string[], deps: CliDependencies = {}): Promise<number> {
  const writeOut = deps.writeOut ?? ((text) => process.stdout.write(text));
  const writeErr = deps.writeErr ?? ((text) => process.stderr.write(text));

  let options: CliOptions;
  try {
    options = parseCliArgs(argv);
  } catch (error) {
    writeErr(`error: ${errorMessage(error)}\n\n${USAGE}\n`);
    return 2;
  }

  if (options.help) {
    writeOut(`${USAGE}\n`);
    return 0;
  }

  const openBrowser = deps.openBrowser ?? openSpiderBrowser;
  let session: BrowserSession | null = null;
  let summary: SerpSummary;

  try {
    session = await openBrowser();
    summary = await scrapeGoogleSerp(session.page, {
      query: options.query,
      pages: options.pages,
      sleep: deps.sleep,
      onPageOutcome: (outcome) => writeErr(formatOutcome(outcome)),
    });
  } catch (error) {
    const reason = `browser session failed: ${errorMessage(error)}`;
    writeErr(`error: ${reason}\n`);
    summary = failureSummary(options.query, options.pages, reason);
  } finally {
    if (session) {
      try {
        await session.close();
      } catch {
        // Closing is best-effort; the summary is what matters.
      }
    }
  }

  writeOut(`${JSON.stringify(summary, null, 2)}\n`);
  return exitCodeForSummary(summary);
}

function isDirectRun(): boolean {
  const entry = process.argv[1];
  if (!entry) return false;
  try {
    return pathToFileURL(resolve(entry)).href === import.meta.url;
  } catch {
    return false;
  }
}

if (isDirectRun()) {
  process.exitCode = await runCli(process.argv.slice(2));
}
