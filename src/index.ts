// note
import { Octokit } from "@octokit/rest";
import * as cheerio from "cheerio";
import { URL } from "node:url";
import TurndownService from "turndown";

import fetch, { RequestInit } from "node-fetch";

const turndownService = new TurndownService();
turndownService.remove("script");

/**
 * Options for the web crawler.
 */
export interface CrawlOptions {
  /**
   * Extract specific content using CSS selector
   */
  extract?: string;

  /**
   * Maximum number of concurrent connections allowed.
   */
  maxConnections: number;

  /**
   * Path names to exclude
   */
  exclude: string[];

  /**
   * Whether to stop the crawling process on the first encountered error.
   */
  breakOnError: boolean;

  /**
   * Whether to enable logging during the crawling process.
   */
  logEnabled: boolean;

  /**
   * Fetch options
   */
  fetchOptions: RequestInit;
}

const IS_GITHUB_REPO =
  /^https:\/\/github\.com\/([^/]+)\/([^/]+)\/tree\/([^/]+)/;

/**
 * A page whose fetch failed while breakOnError was disabled.
 */
export interface FailedPage {
  /**
   * Full address of the page that could not be fetched.
   */
  path: string;
  /**
   * Error message taken from the original error's message or string form.
   */
  error: string;
}

/**
 * Summary returned after the crawl generator finishes.
 */
export interface CrawlResult {
  /**
   * Every page that failed to fetch when breakOnError was false.
   */
  failures: FailedPage[];
}

/**
 * Normalize a URL to the canonical key used for queue de-duplication.
 *
 * Links that point at the same document collapse to the same key:
 * the hash and query string are ignored, the trailing slash is removed
 * and a trailing index.html / index.htm segment is stripped. Accepts both
 * path names (ordinary sites) and full URLs (GitHub repository entries).
 */
export function canonicalizeUrl(link: string, base?: string): string {
  const parsedUrl = new URL(link, base);
  parsedUrl.hash = "";
  parsedUrl.search = "";
  let pathname = parsedUrl.pathname;
  pathname = pathname.replace(/\/index\.(html|htm)$/i, "/");
  pathname = pathname.replace(/\/+$/, "");
  if (pathname === "") {
    pathname = "/";
  }
  return `${parsedUrl.origin}${pathname}`;
}

export async function* crawlWebsite(
  startUrl: string,
  options_?: Partial<CrawlOptions>,
): AsyncGenerator<Page, CrawlResult, unknown> {
  const options: CrawlOptions = {
    maxConnections: 6,
    exclude: [],
    fetchOptions: {},
    breakOnError: true,
    logEnabled: true,
    ...(options_ || {}),
  };

  const startUrlObj = new URL(startUrl);
  startUrl = normalizeStartUrl(startUrl);

  const paths: string[] = [];
  const enqueued = new Set<string>();
  const enqueue = (link: string) => {
    const key = canonicalizeUrl(link, startUrl);
    if (!enqueued.has(key)) {
      enqueued.add(key);
      paths.push(link);
    }
  };

  if (IS_GITHUB_REPO.test(startUrl)) {
    const ghPaths = await crawlGHTree(startUrlObj, options.exclude);
    for (const ghPath of ghPaths) {
      enqueue(ghPath);
    }
  } else {
    enqueue(startUrlObj.pathname);
  }

  const failures: FailedPage[] = [];

  let index = 0;
  while (index < paths.length) {
    const batch = paths.slice(index, index + options.maxConnections);

    const promises = batch.map((path) => crawlPage(startUrl, path, options));

    const results = await Promise.all(promises);

    for (const result of results) {
      if (result.ok === false) {
        failures.push({
          path: new URL(result.path, startUrlObj).toString(),
          error: result.error,
        });
        continue;
      }

      const { links, text, path } = result;
      // A failed fetch never yields a Page; an empty body is skipped too.
      if (text !== "") {
        yield {
          path: new URL(path, startUrlObj).toString(),
          text,
        };
      }
      for (let link of links) {
        enqueue(link);
      }
    }

    index += batch.length;
  }
  if (options.logEnabled) {
    console.log("✨ Crawl completed");
  }

  return { failures };
}

async function crawlGHTree(startUrl: URL, exclude: string[]) {
  const octokit = new Octokit({
    auth: undefined,
  });

  let [_, owner, repo, _scope, branch, ...parts] = startUrl.pathname.split("/");
  const rootPath = parts.join("/");

  const tree = await octokit.request(
    "GET /repos/{owner}/{repo}/git/trees/{tree_sha}",
    {
      owner,
      repo,
      tree_sha: branch,
      headers: {
        "X-GitHub-Api-Version": "2022-11-28",
      },
      recursive: "true",
    },
  );

  const paths = tree.data.tree
    .filter(
      (file) =>
        file.type === "blob" &&
        (file.path?.endsWith(".md") || file.path?.endsWith(".MD")) &&
        file.path.startsWith(rootPath) &&
        !shouldExcludeLink(file.path, exclude),
    )
    .map(
      (file) =>
        `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/${file.path}`,
    );

  return paths;
}

export interface Page {
  path: string;
  text: string;
}

type CrawlPageResult =
  | { ok: true; path: string; text: string; links: string[] }
  | { ok: false; path: string; error: string };

async function crawlPage(
  startUrl: string,
  path: string,
  options: CrawlOptions,
): Promise<CrawlPageResult> {
  const location = new URL(path, startUrl).toString();

  let html = "";

  try {
    const response = await fetch(location, options.fetchOptions);
    html = await response.text();
    if (options.logEnabled) {
      console.log(`🚀 Crawled ${location}`);
    }
  } catch (err) {
    if (options.breakOnError) {
      throw err;
    }
    const message = err instanceof Error ? err.message : String(err);
    if (options.logEnabled) {
      console.error(message);
    }
    // No Page is produced for a failed fetch; the caller records it.
    return { ok: false, path, error: message };
  }

  let links: string[] = [];

  if (IS_GITHUB_REPO.test(startUrl)) {
    return {
      ok: true,
      path,
      text: html,
      links,
    };
  }

  const $ = cheerio.load(html);

  $("a").each((_, element) => {
    try {
      const href = $(element).attr("href");
      if (!href || href.startsWith("#")) {
        return;
      }
      const parsedUrl = new URL(href, location);
      if (parsedUrl.toString().startsWith(startUrl)) {
        const link = parsedUrl.pathname;
        if (!shouldExcludeLink(link, options.exclude)) {
          links.push(link);
        }
      }
    } catch {}
  });

  let text = html;
  if (options.extract) {
    text = $(options.extract)?.html();
  }
  text = turndownService.turndown(text);

  return {
    ok: true,
    path,
    text,
    links: [...new Set(links)],
  };
}

function shouldExcludeLink(link: string, exclude: string[]) {
  if (link.includes("#")) {
    return true;
  }
  const parts = link.replace(/\/$/, "").split("/");
  let name = (parts[parts.length - 1] || "").toLowerCase();

  for (const excludeName of exclude) {
    let cond = false;
    if (/\.[^.]+$/.test(excludeName)) {
      cond = excludeName.toLowerCase() === name.toLowerCase();
    } else {
      cond =
        excludeName.toLowerCase() ===
        name.toLowerCase().replace(/\.[^.]+$/, "");
    }
    if (cond) {
      return true;
    }
  }
  return false;
}

function normalizeStartUrl(startUrl: string) {
  const parsedUrl = new URL(startUrl);
  parsedUrl.search = "";
  parsedUrl.hash = "";
  let lastSlashIndex = parsedUrl.pathname.lastIndexOf("/");
  if (lastSlashIndex !== -1) {
    parsedUrl.pathname = parsedUrl.pathname.substring(0, lastSlashIndex + 1);
  }
  return parsedUrl.toString();
}
// fix tweak
// fix note
