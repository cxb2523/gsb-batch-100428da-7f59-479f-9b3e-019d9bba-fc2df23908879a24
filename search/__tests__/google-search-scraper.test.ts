/**
 * Local regression tests for the Google search scraper.
 *
 * These never touch Google. Each test feeds the real extraction, classification,
 * merge, and CLI code a synthetic HTML fixture (search/__tests__/fixtures) via
 * a stub page object, so the pagination and outcome logic can be reproduced
 * offline. Fixtures are test doubles, not captured live responses.
 *
 * Run: npm run test:google-search
 */

import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  buildGoogleSerpUrl,
  classifySerpPayload,
  DEFAULT_PAGES,
  DEFAULT_QUERY,
  exitCodeForSummary,
  parseCliArgs,
  parseRawPayload,
  runCli,
  scrapeGoogleSerp,
  SERP_EXTRACT_SCRIPT,
  type PageOutcome,
  type SerpPage,
  type SerpSummary,
} from "../google-search-scraper.js";
import { fixture, routesFor, StubSerpPage } from "./fixture-page.js";

describe("CLI argument parsing", () => {
  test("defaults to the original query and a single page", () => {
    assert.deepEqual(parseCliArgs([]), { query: DEFAULT_QUERY, pages: DEFAULT_PAGES });
  });

  test("accepts positional query and page count", () => {
    assert.deepEqual(parseCliArgs(["rust web crawler", "3"]), { query: "rust web crawler", pages: 3 });
  });

  test("accepts long and short flags, including --flag=value forms", () => {
    assert.deepEqual(parseCliArgs(["--query", "nextjs scraper", "--pages", "2"]), {
      query: "nextjs scraper",
      pages: 2,
    });
    assert.deepEqual(parseCliArgs(["--query=headless browser", "--pages=4"]), {
      query: "headless browser",
      pages: 4,
    });
    assert.deepEqual(parseCliArgs(["-q", "proxies", "-p", "5"]), { query: "proxies", pages: 5 });
  });

  test("flags override positionals", () => {
    assert.deepEqual(parseCliArgs(["ignored", "9", "--query", "flags win", "--pages", "2"]), {
      query: "flags win",
      pages: 2,
    });
  });

  test("rejects invalid page counts and empty queries with readable errors", () => {
    assert.throws(() => parseCliArgs(["q", "0"]), /between 1 and 100/);
    assert.throws(() => parseCliArgs(["q", "abc"]), /between 1 and 100/);
    assert.throws(() => parseCliArgs(["q", "2.5"]), /between 1 and 100/);
    assert.throws(() => parseCliArgs(["--pages", "999"]), /between 1 and 100/);
    assert.throws(() => parseCliArgs(["--pages"]), /requires a value/);
    assert.throws(() => parseCliArgs(["--bogus"]), /unknown option/);
    assert.throws(() => parseCliArgs(["a", "b", "c"]), /unexpected argument/);
    assert.throws(() => parseCliArgs(["   "]), /must not be empty/);
    assert.throws(() => parseCliArgs(["--query", ""]), /must not be empty/);
  });

  test("supports --help", () => {
    assert.equal(parseCliArgs(["--help"]).help, true);
  });
});

describe("result page URLs", () => {
  test("encodes the query and steps start by 10", () => {
    assert.equal(buildGoogleSerpUrl("web scraping api", 1), "https://www.google.com/search?q=web%20scraping%20api");
    assert.equal(
      buildGoogleSerpUrl("c++ tutorials", 3),
      "https://www.google.com/search?q=c%2B%2B%20tutorials&start=20"
    );
  });
});

describe("page classification", () => {
  test("classifies each fixture as expected", async () => {
    const expectations: Array<[string, string, RegExp | null]> = [
      ["serp-page1.html", "ok", null],
      ["serp-empty-rso.html", "empty", /#rso had 1 item\(s\) but none were extractable/],
      ["serp-captcha.html", "blocked", /captcha or block page:.*#captcha-form.*unusual traffic/],
      ["serp-consent.html", "blocked", /consent\/interstitial page:.*consent\.google\.com.*before you continue to google/],
      ["serp-no-results.html", "empty", /no matching documents/],
    ];

    for (const [file, outcome, reason] of expectations) {
      const page = new StubSerpPage(routesFor("web scraping api", [fixture(file)]));
      await page.goto(buildGoogleSerpUrl("web scraping api", 1));
      const raw = parseRawPayload(await page.evaluate(SERP_EXTRACT_SCRIPT));
      const classification = classifySerpPayload(raw);
      assert.equal(classification.outcome, outcome, file);
      if (reason) assert.match(classification.reason ?? "", reason, file);
    }
  });

  test("a consent banner above real results does not discard those results", async () => {
    const page = new StubSerpPage(routesFor("q", [fixture("serp-consent-with-results.html")]));
    await page.goto(buildGoogleSerpUrl("q", 1));
    const raw = parseRawPayload(await page.evaluate(SERP_EXTRACT_SCRIPT));

    assert.ok(raw.softBlockers.length > 0, "consent markers should still be reported");
    assert.equal(raw.hardBlockers.length, 0);
    const classification = classifySerpPayload(raw);
    assert.equal(classification.outcome, "ok");
    assert.equal(classification.resultCount, 2);
  });

  test("a captcha page is blocked even if it happens to contain result-looking markup", async () => {
    const page = new StubSerpPage(routesFor("q", [fixture("serp-captcha.html")]));
    await page.goto(buildGoogleSerpUrl("q", 1));
    const raw = parseRawPayload(await page.evaluate(SERP_EXTRACT_SCRIPT));
    raw.results.push({ title: "Bogus", url: "https://example.com/bogus", snippet: null });

    const classification = classifySerpPayload(raw);
    assert.equal(classification.outcome, "blocked");
    assert.equal(classification.resultCount, 0);
  });

  test("a page with neither #rso nor a no-results notice is an error, not empty", async () => {
    const page = new StubSerpPage(routesFor("q", ["<!doctype html><html><body><p>hello</p></body></html>"]));
    await page.goto(buildGoogleSerpUrl("q", 1));
    const raw = parseRawPayload(await page.evaluate(SERP_EXTRACT_SCRIPT));
    const classification = classifySerpPayload(raw);
    assert.equal(classification.outcome, "error");
    assert.match(classification.reason!, /#rso not found/);
  });

  test("page 1 fixture extracts titles, URLs, snippets and the featured field", async () => {
    const page = new StubSerpPage(routesFor("web scraping api", [fixture("serp-page1.html")]));
    await page.goto(buildGoogleSerpUrl("web scraping api", 1));
    const raw = parseRawPayload(await page.evaluate(SERP_EXTRACT_SCRIPT));

    assert.equal(raw.results.length, 3, "sponsored block must be skipped");
    assert.deepEqual(raw.results[0], {
      title: "ScrapingBee - Web Scraping API",
      url: "https://www.scrapingbee.com/",
      snippet: "ScrapingBee is a web scraping API that handles proxies and browsers.",
    });
    assert.equal(raw.results[1]!.snippet, "ScraperAPI rotates proxies and renders JavaScript for you.");
    assert.equal(raw.featured, "Web scraping APIs fetch pages at scale and return structured data.");
  });

  test("malformed evaluate output is rejected with a descriptive error", () => {
    assert.throws(() => parseRawPayload(null), /expected a JSON string/);
    assert.throws(() => parseRawPayload("{not json"), /invalid JSON/);
    assert.throws(() => parseRawPayload('"a string"'), /not an object/);
  });
});

describe("multi-page scrape", () => {
  test("merges pages in order, drops duplicate URLs, renumbers positions", async () => {
    const query = "web scraping api";
    const page = new StubSerpPage(
      routesFor(query, [fixture("serp-page1.html"), fixture("serp-page2.html")])
    );
    const outcomes: PageOutcome[] = [];

    const summary = await scrapeGoogleSerp(page, {
      query,
      pages: 2,
      pageDelayMs: 0,
      onPageOutcome: (outcome) => outcomes.push(outcome),
    });

    assert.deepEqual(page.visited, [buildGoogleSerpUrl(query, 1), buildGoogleSerpUrl(query, 2)]);

    assert.equal(summary.results.length, 5);
    assert.deepEqual(
      summary.results.map((result) => [result.position, result.page, result.url]),
      [
        [1, 1, "https://www.scrapingbee.com/"],
        [2, 1, "https://www.scraperapi.com/"],
        [3, 1, "https://www.zenrows.com/"],
        [4, 2, "https://brightdata.com/products/web-scraper"],
        [5, 2, "https://apify.com/"],
      ]
    );
    assert.equal(summary.featured, "Web scraping APIs fetch pages at scale and return structured data.");
    assert.equal(summary.status, "ok");
    assert.deepEqual(summary.pageOutcomes, [
      { page: 1, outcome: "ok", resultCount: 3 },
      { page: 2, outcome: "ok", resultCount: 3 },
    ]);
    assert.deepEqual(outcomes, summary.pageOutcomes);
  });

  test("blocked and failed pages do not stop later pages", async () => {
    const query = "web scraping api";
    const page = new StubSerpPage(
      routesFor(query, [
        fixture("serp-captcha.html"),
        new Error("navigation timed out after 30s"),
        fixture("serp-page1.html"),
      ])
    );
    const summary = await scrapeGoogleSerp(page, { query, pages: 3, pageDelayMs: 0 });

    assert.equal(summary.results.length, 3, "results from page 3 are still collected");
    assert.deepEqual(
      summary.pageOutcomes.map(({ page, outcome, resultCount }) => ({ page, outcome, resultCount })),
      [
        { page: 1, outcome: "blocked", resultCount: 0 },
        { page: 2, outcome: "error", resultCount: 0 },
        { page: 3, outcome: "ok", resultCount: 3 },
      ]
    );
    assert.match(summary.pageOutcomes[0]!.reason!, /unusual traffic/);
    assert.equal(
      summary.pageOutcomes[1]!.reason,
      "navigation or extraction failed: navigation timed out after 30s"
    );
    assert.equal(summary.pageOutcomes[2]!.reason, undefined);
    assert.equal(summary.status, "ok");
    assert.equal(exitCodeForSummary(summary), 0);
  });

  test("every page blocked or failed still returns a summary and exit code 1", async () => {
    const query = "web scraping api";
    const page = new StubSerpPage(
      routesFor(query, [fixture("serp-captcha.html"), new Error("socket hang up"), fixture("serp-consent.html")])
    );
    const summary = await scrapeGoogleSerp(page, { query, pages: 3, pageDelayMs: 0 });

    assert.deepEqual(summary.results, []);
    assert.deepEqual(summary.pageOutcomes.map((outcome) => outcome.outcome), ["blocked", "error", "blocked"]);
    assert.equal(summary.status, "blocked_or_error");
    assert.equal(exitCodeForSummary(summary), 1);
    assert.deepEqual(page.visited, [
      buildGoogleSerpUrl(query, 1),
      buildGoogleSerpUrl(query, 2),
      buildGoogleSerpUrl(query, 3),
    ]);
  });

  test("a page whose result area is present but empty is reported as empty, exit code 0", async () => {
    const query = "web scraping api";
    const page = new StubSerpPage(routesFor(query, [fixture("serp-empty-rso.html")]));
    const summary = await scrapeGoogleSerp(page, { query, pages: 1, pageDelayMs: 0 });

    assert.equal(summary.results.length, 0);
    assert.equal(summary.pageOutcomes[0]!.outcome, "empty");
    assert.equal(summary.pageOutcomes[0]!.resultCount, 0);
    assert.equal(summary.status, "no_results");
    assert.equal(exitCodeForSummary(summary), 0);
  });

  test("a genuine no-results page is empty, while all-blocked is distinguishable", async () => {
    const query = "web scraping api zzqx";
    const emptyPage = new StubSerpPage(routesFor(query, [fixture("serp-no-results.html")]));
    const emptySummary = await scrapeGoogleSerp(emptyPage, { query, pages: 1, pageDelayMs: 0 });
    assert.equal(emptySummary.pageOutcomes[0]!.outcome, "empty");
    assert.equal(exitCodeForSummary(emptySummary), 0);

    const blockedPage = new StubSerpPage(routesFor(query, [fixture("serp-captcha.html")]));
    const blockedSummary = await scrapeGoogleSerp(blockedPage, { query, pages: 1, pageDelayMs: 0 });
    assert.equal(blockedSummary.pageOutcomes[0]!.outcome, "blocked");
    assert.equal(exitCodeForSummary(blockedSummary), 1);
  });

  test("mixed empty and error pages: exit code 0 because one page gave a genuine answer", async () => {
    const query = "web scraping api";
    const page = new StubSerpPage(
      routesFor(query, [fixture("serp-empty-rso.html"), new Error("parse failure")])
    );
    const summary = await scrapeGoogleSerp(page, { query, pages: 2, pageDelayMs: 0 });
    assert.equal(summary.status, "no_results");
    assert.equal(exitCodeForSummary(summary), 0);
  });

  test("a page where content() or evaluate() throws is recorded as an error", async () => {
    const query = "web scraping api";
    const throwOn = (method: "content" | "evaluate"): SerpPage => {
      const page = new StubSerpPage(
        routesFor(query, [fixture("serp-page1.html"), fixture("serp-page2.html")])
      );
      page[method] = async () => {
        throw new Error(`${method} exploded`);
      };
      return page;
    };

    const contentFailure = await scrapeGoogleSerp(throwOn("content"), { query, pages: 2, pageDelayMs: 0 });
    assert.deepEqual(contentFailure.pageOutcomes.map((o) => [o.page, o.outcome]), [
      [1, "error"],
      [2, "error"],
    ]);
    assert.match(contentFailure.pageOutcomes[0]!.reason!, /content exploded/);
    assert.equal(exitCodeForSummary(contentFailure), 1);

    const evaluateFailure = await scrapeGoogleSerp(throwOn("evaluate"), { query, pages: 2, pageDelayMs: 0 });
    assert.equal(evaluateFailure.pageOutcomes[0]!.outcome, "error");
    assert.match(evaluateFailure.pageOutcomes[0]!.reason!, /evaluate exploded/);
  });

  test("sleep is called between pages but not before the first", async () => {
    const query = "web scraping api";
    const page = new StubSerpPage(routesFor(query, [fixture("serp-page1.html"), fixture("serp-page2.html")]));
    const slept: number[] = [];
    await scrapeGoogleSerp(page, {
      query,
      pages: 2,
      pageDelayMs: 25,
      sleep: async (ms) => {
        slept.push(ms);
      },
    });
    assert.deepEqual(slept, [25]);
  });
});

describe("runCli", () => {
  function capture(): { out: string[]; err: string[]; deps: { writeOut: (t: string) => void; writeErr: (t: string) => void } } {
    const out: string[] = [];
    const err: string[] = [];
    return {
      out,
      err,
      deps: { writeOut: (text) => out.push(text), writeErr: (text) => err.push(text) },
    };
  }

  test("prints the JSON summary to stdout and progress to stderr", async () => {
    const { out, err, deps } = capture();
    const query = "web scraping api";
    const page = new StubSerpPage(routesFor(query, [fixture("serp-page1.html")]));

    const code = await runCli([query, "1"], {
      ...deps,
      sleep: async () => undefined,
      openBrowser: async () => ({ page, close: async () => undefined }),
    });

    assert.equal(code, 0);
    const summary = JSON.parse(out.join("")) as SerpSummary;
    assert.equal(summary.query, query);
    assert.equal(summary.results.length, 3);
    assert.deepEqual(summary.pageOutcomes, [{ page: 1, outcome: "ok", resultCount: 3 }]);
    assert.match(err.join(""), /page 1: ok \(3 result\(s\)\)/);
  });

  test("all-blocked run still prints a summary and exits 1", async () => {
    const { out, deps } = capture();
    const query = "web scraping api";
    const page = new StubSerpPage(
      routesFor(query, [fixture("serp-captcha.html"), fixture("serp-consent.html")])
    );

    const code = await runCli([query, "2"], {
      ...deps,
      sleep: async () => undefined,
      openBrowser: async () => ({ page, close: async () => undefined }),
    });

    assert.equal(code, 1);
    const summary = JSON.parse(out.join("")) as SerpSummary;
    assert.equal(summary.status, "blocked_or_error");
    assert.deepEqual(summary.results, []);
    assert.deepEqual(summary.pageOutcomes.map((outcome) => outcome.outcome), ["blocked", "blocked"]);
    assert.equal(summary.pagesRequested, 2);
  });

  test("a browser session that never opens still produces a summary and exit 1", async () => {
    const { out, err, deps } = capture();
    const code = await runCli(["--query", "anything", "--pages", "2"], {
      ...deps,
      openBrowser: async () => {
        throw new Error("SPIDER_API_KEY is not set");
      },
    });

    assert.equal(code, 1);
    const summary = JSON.parse(out.join("")) as SerpSummary;
    assert.equal(summary.query, "anything");
    assert.equal(summary.pageOutcomes.length, 2);
    assert.equal(summary.pageOutcomes[0]!.outcome, "error");
    assert.match(summary.pageOutcomes[0]!.reason!, /SPIDER_API_KEY is not set/);
    assert.match(err.join(""), /browser session failed/);
  });

  test("invalid arguments exit 2 with usage, before opening a browser", async () => {
    const { out, err, deps } = capture();
    let opened = false;
    const code = await runCli(["q", "0"], {
      ...deps,
      openBrowser: async () => {
        opened = true;
        throw new Error("should not open");
      },
    });

    assert.equal(code, 2);
    assert.equal(opened, false);
    assert.equal(out.join(""), "");
    assert.match(err.join(""), /page count must be an integer/);
    assert.match(err.join(""), /Usage:/);
  });

  test("--help exits 0 without opening a browser", async () => {
    const { out, deps } = capture();
    let opened = false;
    const code = await runCli(["--help"], {
      ...deps,
      openBrowser: async () => {
        opened = true;
        throw new Error("should not open");
      },
    });
    assert.equal(code, 0);
    assert.equal(opened, false);
    assert.match(out.join(""), /Usage:/);
    assert.match(out.join(""), /Exit code 0/);
  });
});
