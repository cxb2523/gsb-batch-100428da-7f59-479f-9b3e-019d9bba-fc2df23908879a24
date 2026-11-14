import type { CollectionOutcome, SourceCandidate } from "./contracts";
import { CollectionOutcomeTracker } from "./outcomes";

type TaskSourceConfig = {
  naturalLanguageRequest: string;
  sources: string[];
  keywords: string[];
  topics: string[];
  sourceFilters: { include?: string[]; exclude?: string[] };
};

function decodeHtml(value: string) {
  return value
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/<[^>]*>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function nodeValue(xml: string, tag: string) {
  const match = xml.match(
    new RegExp(`<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, "i")
  );
  return match ? decodeHtml(match[1]) : "";
}

function parseDate(value: string) {
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? undefined : new Date(timestamp);
}

export function isSafePublicHttpUrl(rawUrl: string) {
  try {
    const url = new URL(rawUrl);
    const host = url.hostname.toLowerCase();
    if (url.protocol !== "http:" && url.protocol !== "https:") return false;
    if (
      host === "localhost" ||
      host === "::1" ||
      host.endsWith(".localhost") ||
      host === "metadata.google.internal"
    )
      return false;
    if (
      /^(127\.|0\.0\.0\.0$|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.)/.test(
        host
      )
    )
      return false;
    return true;
  } catch {
    return false;
  }
}

function safeHostname(rawUrl: string) {
  try {
    return new URL(rawUrl).hostname;
  } catch {
    return rawUrl;
  }
}

function parseRss(xml: string, sourceName: string): SourceCandidate[] {
  return xml
    .split(/<item[\s>]/i)
    .slice(1)
    .map(item => {
      const title = nodeValue(item, "title");
      const url = nodeValue(item, "link");
      const text =
        nodeValue(item, "description") || nodeValue(item, "content:encoded");
      return {
        title: title || "Untitled RSS item",
        url,
        sourceName,
        text,
        publishedAt: parseDate(nodeValue(item, "pubDate")),
      };
    })
    .filter(item => item.url && item.text);
}

/**
 * Fetches a public HTTP(S) resource. The safety check is the same one used by
 * every other guard in this module — recording a rejection never relaxes it.
 */
async function fetchText(url: string) {
  if (!isSafePublicHttpUrl(url))
    throw new Error("Only public HTTP(S) source URLs are permitted");
  const response = await fetch(url, {
    headers: {
      "User-Agent": "Intelis-Agent/1.0 (responsible research monitoring)",
    },
    signal: AbortSignal.timeout(12_000),
  });
  if (!response.ok)
    throw new Error(`Source request failed (${response.status})`);
  return response.text();
}

async function collectWebSearch(
  query: string,
  limit: number,
  tracker: CollectionOutcomeTracker
) {
  const endpoint = new URL("https://api.gdeltproject.org/api/v2/doc/doc");
  endpoint.searchParams.set("query", query.slice(0, 512));
  endpoint.searchParams.set("mode", "artlist");
  endpoint.searchParams.set("format", "json");
  endpoint.searchParams.set("maxrecords", String(Math.min(limit, 25)));
  const response = await fetch(endpoint, {
    signal: AbortSignal.timeout(12_000),
  });
  if (!response.ok)
    throw new Error(`Web discovery request failed (${response.status})`);
  const payload = (await response.json()) as {
    articles?: Array<{
      title?: string;
      url?: string;
      domain?: string;
      seendate?: string;
    }>;
  };
  const articles = (payload.articles ?? [])
    .filter(article => Boolean(article.title && article.url))
    .slice(0, limit);
  const materialized = await Promise.allSettled(
    articles.map(async article => {
      const title = article.title as string;
      const url = article.url as string;
      const sourceName = article.domain || safeHostname(url);
      const candidate = { title, url, sourceName };
      if (!isSafePublicHttpUrl(url)) {
        tracker.record(
          candidate,
          "rejected_by_safety",
          "URL does not resolve to a public HTTP(S) target."
        );
        return null;
      }
      try {
        const html = await fetchText(url);
        const text = decodeHtml(html).slice(0, 15000);
        if (text.length <= 80) {
          tracker.record(
            candidate,
            "content_too_short",
            `Extracted body was ${text.length} characters (minimum 80).`
          );
          return null;
        }
        return {
          ...candidate,
          text,
          publishedAt: parseDate(article.seendate || ""),
        } satisfies SourceCandidate;
      } catch (error) {
        tracker.record(
          candidate,
          "request_failed",
          error instanceof Error ? error.message : "Request failed"
        );
        return null;
      }
    })
  );
  return materialized.flatMap(result =>
    result.status === "fulfilled" && result.value ? [result.value] : []
  );
}

async function collectRss(urls: string[], tracker: CollectionOutcomeTracker) {
  const feeds = await Promise.allSettled(
    urls.map(async url => parseRss(await fetchText(url), safeHostname(url)))
  );
  return feeds.flatMap((result, index) => {
    if (result.status === "fulfilled") return result.value;
    const url = urls[index];
    tracker.record(
      { url, sourceName: safeHostname(url), title: url },
      "request_failed",
      result.reason instanceof Error
        ? result.reason.message
        : "RSS feed request failed"
    );
    return [];
  });
}

async function collectDirectPages(
  urls: string[],
  tracker: CollectionOutcomeTracker
) {
  const items = await Promise.allSettled(
    urls.map(async url => {
      const candidate = { url, sourceName: safeHostname(url), title: url };
      try {
        const html = await fetchText(url);
        const title = nodeValue(html, "title") || safeHostname(url);
        return {
          ...candidate,
          title,
          text: decodeHtml(html).slice(0, 15000),
        } satisfies SourceCandidate;
      } catch (error) {
        tracker.record(
          candidate,
          "request_failed",
          error instanceof Error ? error.message : "Request failed"
        );
        throw error;
      }
    })
  );
  return items.flatMap(item =>
    item.status === "fulfilled" ? [item.value] : []
  );
}

async function collectNewsApi(query: string) {
  if (!process.env.NEWS_API_KEY) return [];
  const url = new URL("https://newsapi.org/v2/everything");
  url.searchParams.set("q", query);
  url.searchParams.set("pageSize", "20");
  url.searchParams.set("sortBy", "publishedAt");
  const response = await fetch(url, {
    headers: { "X-Api-Key": process.env.NEWS_API_KEY },
  });
  if (!response.ok)
    throw new Error(`News API request failed (${response.status})`);
  const payload = (await response.json()) as {
    articles?: Array<{
      title?: string;
      url?: string;
      description?: string;
      content?: string;
      source?: { name?: string };
      publishedAt?: string;
    }>;
  };
  return (payload.articles ?? [])
    .filter(
      article =>
        article.title && article.url && (article.content || article.description)
    )
    .map(article => ({
      title: article.title!,
      url: article.url!,
      sourceName: article.source?.name || safeHostname(article.url!),
      text: article.content || article.description || "",
      publishedAt: parseDate(article.publishedAt || ""),
    }));
}

/**
 * Live crawl probe. Candidates that fail the public HTTP(S) guard, time out, or
 * return a non-OK response are tracked as rejected / failed and dropped; the
 * returned list still contains only reachable candidates.
 */
export async function crawlSourceCandidates(
  candidates: SourceCandidate[],
  tracker?: CollectionOutcomeTracker
) {
  const ledger = tracker ?? new CollectionOutcomeTracker();
  const crawlResults = await Promise.allSettled(
    candidates.map(async candidate => {
      if (!isSafePublicHttpUrl(candidate.url)) {
        ledger.record(
          candidate,
          "rejected_by_safety",
          "URL does not resolve to a public HTTP(S) target."
        );
        return null;
      }
      try {
        const response = await fetch(candidate.url, {
          method: "GET",
          headers: {
            "User-Agent": "Intelis-Agent/1.0 (responsible research monitoring)",
            Range: "bytes=0-1024",
          },
          redirect: "follow",
          signal: AbortSignal.timeout(10_000),
        });
        if (!response.ok) {
          ledger.record(
            candidate,
            "request_failed",
            `Live crawl returned HTTP ${response.status}.`
          );
          return null;
        }
        return candidate;
      } catch (error) {
        ledger.record(
          candidate,
          "request_failed",
          error instanceof Error ? error.message : "Live crawl request failed"
        );
        return null;
      }
    })
  );
  return crawlResults.flatMap(result =>
    result.status === "fulfilled" && result.value ? [result.value] : []
  );
}

export type CollectSourceCandidatesResult = {
  candidates: SourceCandidate[];
  outcomes: CollectionOutcome[];
};

/**
 * Discovers candidates across web search, RSS feeds, direct pages and NewsAPI.
 * The returned `candidates` list is unchanged from the previous behavior; the
 * per-candidate `outcomes` ledger records the final state of every candidate
 * that was attempted (including those rejected or dropped).
 */
export async function collectSourceCandidates(
  task: TaskSourceConfig,
  limit: number,
  tracker: CollectionOutcomeTracker = new CollectionOutcomeTracker()
): Promise<CollectSourceCandidatesResult> {
  const includeUrls = task.sourceFilters.include ?? [];
  for (const url of includeUrls) {
    if (!isSafePublicHttpUrl(url)) {
      tracker.record(
        { url, sourceName: safeHostname(url), title: url },
        "rejected_by_safety",
        "URL does not resolve to a public HTTP(S) target."
      );
    }
  }
  const safeIncludeUrls = includeUrls.filter(isSafePublicHttpUrl);
  const excluded = new Set(task.sourceFilters.exclude ?? []);
  const query =
    [
      ...task.keywords,
      ...task.topics,
      task.naturalLanguageRequest.slice(0, 250),
    ]
      .filter(Boolean)
      .join(" OR ") || "technology";
  const sources = new Set(task.sources);
  const jobs: Promise<SourceCandidate[]>[] = [];
  if (sources.has("rss")) jobs.push(collectRss(safeIncludeUrls, tracker));
  if (sources.has("web"))
    jobs.push(
      collectWebSearch(query, Math.min(limit, 10), tracker),
      collectDirectPages(safeIncludeUrls, tracker)
    );
  if (sources.has("news_api")) jobs.push(collectNewsApi(query));
  const results = await Promise.allSettled(jobs);
  const candidates = results
    .flatMap(result => (result.status === "fulfilled" ? result.value : []))
    .filter(
      item => !Array.from(excluded).some(domain => item.url.includes(domain))
    )
    .slice(0, limit);
  return { candidates, outcomes: tracker.outcomes() };
}
