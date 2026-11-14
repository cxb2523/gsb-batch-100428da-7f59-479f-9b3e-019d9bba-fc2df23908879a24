/**
 * Pure crawl engine for streaming JSONL responses from the Spider API.
 *
 * This module is intentionally free of React/DOM UI concerns so that the
 * streaming, attribution and error-handling behaviour can be unit tested
 * with locally fabricated JSONL samples (no real Spider API call needed).
 */

export interface CrawlPage {
  url: string;
  content?: string;
  error?: string;
  status?: number;
  /** The user-entered site URL this page was attributed to. */
  sourceUrl?: string;
  [key: string]: unknown;
}

export type CrawlOutcome = "completed" | "interrupted" | "failed";

export interface CrawlResult {
  outcome: CrawlOutcome;
  /** Pages received so far, de-duplicated by URL (latest content wins). */
  pages: CrawlPage[];
  /** Number of unique pages currently held. */
  receivedCount: number;
  /** Human-readable failure reason when the crawl did not complete. */
  error?: string;
  durationMs: number;
}

export interface RunCrawlParams {
  apiUrl: string;
  body: Record<string, unknown>;
  headers: Record<string, string>;
  /** Normalized list of URLs the user entered (see parseUrlList). */
  inputUrls: string[];
  /** Injectable fetch so tests can feed fabricated JSONL streams. */
  fetchImpl?: typeof fetch;
  /** Called with the live page list every time a valid line is parsed. */
  onPage?: (pages: CrawlPage[]) => void;
}

/** A mutable holder for the partial JSONL line carried across chunks. */
export interface StreamBuffer {
  current: string;
}

export function createStreamBuffer(): StreamBuffer {
  return { current: "" };
}

/**
 * Normalize the comma-separated user input into absolute URLs, dropping
 * blank entries.
 */
export function parseUrlList(input: string): string[] {
  return input
    .trim()
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) =>
      item.startsWith("http://") || item.startsWith("https://")
        ? item
        : `https://${item}`
    );
}

function normalizeHost(hostname: string): string {
  return hostname.replace(/^www\./, "");
}

/**
 * Attribute a crawled page to one of the URLs the user typed in.
 *
 * Spider expands each input site into many pages, so the page URL rarely
 * equals an input URL exactly. Matching strategy:
 *   1. A single input owns every page.
 *   2. Same-hostname inputs: longest matching path prefix wins (so
 *      https://a.example/blog and https://a.example/docs stay separable).
 *   3. Subdomain suffix match (pages under sub.example.co match example.co).
 *   4. Fallback to the first input so attribution is never empty.
 */
export function matchSourceUrl(pageUrl: string, inputUrls: string[]): string {
  if (inputUrls.length === 0) return pageUrl;
  if (inputUrls.length === 1) return inputUrls[0];

  let page: URL;
  try {
    page = new URL(pageUrl);
  } catch {
    return inputUrls[0];
  }

  // Same hostname — distinguish by path prefix.
  let best: { input: string; prefixLen: number } | null = null;
  for (const input of inputUrls) {
    let candidate: URL;
    try {
      candidate = new URL(input);
    } catch {
      continue;
    }
    if (normalizeHost(candidate.hostname) !== normalizeHost(page.hostname)) {
      continue;
    }
    const inputPath =
      candidate.pathname === "/"
        ? "/"
        : candidate.pathname.replace(/\/+$/, "");
    const pagePath = page.pathname;
    const matches =
      inputPath === "/" ||
      pagePath === inputPath ||
      pagePath.startsWith(inputPath + "/");
    if (matches && (!best || inputPath.length > best.prefixLen)) {
      best = { input, prefixLen: inputPath.length };
    }
  }
  if (best) return best.input;

  // Hostname suffix match for subdomains.
  const pageHost = normalizeHost(page.hostname);
  for (const input of inputUrls) {
    try {
      const host = normalizeHost(new URL(input).hostname);
      if (pageHost === host || pageHost.endsWith("." + host)) {
        return input;
      }
    } catch {
      // ignore malformed input and keep looking
    }
  }

  return inputUrls[0];
}

/**
 * Feed one decoded chunk into the line buffer. Returns the complete lines
 * found in this chunk; the trailing (possibly incomplete) line remains
 * buffered for the next chunk.
 */
export function feedChunk(buffer: StreamBuffer, chunk: string): string[] {
  buffer.current += chunk;
  const lines = buffer.current.split("\n");
  buffer.current = lines.pop() || "";
  return lines;
}

/** Return any non-empty buffered remainder once the stream is done. */
export function flushBuffer(buffer: StreamBuffer): string | null {
  const remaining = buffer.current.trim();
  buffer.current = "";
  return remaining || null;
}

/** Parse one JSONL line; returns null for invalid/URL-less records. */
export function parseJsonlLine(line: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(line);
    if (parsed && typeof parsed === "object" && typeof parsed.url === "string") {
      return parsed as Record<string, unknown>;
    }
    return null;
  } catch {
    return null;
  }
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  try {
    return JSON.stringify(err);
  } catch {
    return String(err);
  }
}

/**
 * Run one crawl against the (possibly mocked) fetch implementation.
 *
 * Pages are upserted by URL as valid JSONL lines arrive, so a repeated URL
 * overwrites the previous record instead of stacking duplicates. A throw
 * from fetch or reader.read() never discards already-received pages: the
 * result is "interrupted" when at least one page arrived, otherwise
 * "failed". A clean stream end resolves as "completed".
 */
export async function runCrawl(params: RunCrawlParams): Promise<CrawlResult> {
  const { apiUrl, body, headers, inputUrls, onPage } = params;
  const fetchImpl = params.fetchImpl ?? fetch;
  const startedAt = performance.now();

  const pages: CrawlPage[] = [];
  const indexByUrl = new Map<string, number>();
  const buffer = createStreamBuffer();

  const finish = (
    outcome: CrawlOutcome,
    error?: string
  ): CrawlResult => ({
    outcome,
    pages,
    receivedCount: pages.length,
    error,
    durationMs: performance.now() - startedAt,
  });

  const upsert = (parsed: Record<string, unknown>) => {
    const url = parsed.url as string;
    const page: CrawlPage = {
      ...(parsed as CrawlPage),
      url,
      sourceUrl: matchSourceUrl(url, inputUrls),
    };
    const existingIndex = indexByUrl.get(url);
    if (existingIndex !== undefined) {
      // Same page URL seen again — newest content replaces the old record.
      pages[existingIndex] = page;
    } else {
      indexByUrl.set(url, pages.length);
      pages.push(page);
    }
    onPage?.(pages);
  };

  let res: Response;
  try {
    res = await fetchImpl(apiUrl + "/crawl", {
      method: "POST",
      body: JSON.stringify(body),
      headers,
    });
  } catch (err) {
    // Nothing could have been received before a response existed.
    return finish("failed", errorMessage(err));
  }

  if (!res.ok) {
    return finish(
      "failed",
      `HTTP ${res.status}${res.statusText ? ` ${res.statusText}` : ""}`
    );
  }

  const reader = res.body?.getReader();
  if (!reader) {
    return finish("completed");
  }
  const decoder = new TextDecoder();

  try {
    while (true) {
      const { done, value } = await reader.read();

      if (done) {
        const remaining = flushBuffer(buffer);
        if (remaining) {
          const parsed = parseJsonlLine(remaining);
          if (parsed) upsert(parsed);
        }
        break;
      }

      const chunk = decoder.decode(value, { stream: true });
      for (const line of feedChunk(buffer, chunk)) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        const parsed = parseJsonlLine(trimmed);
        if (parsed) upsert(parsed);
      }
    }
  } catch (err) {
    // Keep every page already received; only the outcome changes.
    return finish(
      pages.length > 0 ? "interrupted" : "failed",
      errorMessage(err)
    );
  }

  return finish("completed");
}
