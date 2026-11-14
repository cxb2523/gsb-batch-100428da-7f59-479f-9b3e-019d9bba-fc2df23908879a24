const mockParseURL = jest.fn();

// The arrow below defers reading `mockParseURL`, so the factory can run while
// the module-level `const` is still initialising (jest.mock calls are hoisted).
jest.mock("rss-parser", () => ({
  __esModule: true,
  default: jest.fn().mockImplementation(() => ({
    parseURL: (...args: unknown[]) => mockParseURL(...args),
  })),
}));

import { NewsArticle, RSSFeed, RegionFetchResult } from "@/types";
import { globalCache } from "../cache";
import { newsAggregator } from "../news-aggregator";
import {
  ALL_RSS_FEEDS,
  REGION_MAX_SOURCES,
  RSSAggregator,
  rssAggregator,
  selectRegionFeeds,
} from "../rss-aggregator";

const feed = (id: string, country: string, url = `https://feeds.example/${id}`): RSSFeed => ({
  url,
  category: "general",
  source: {
    id,
    name: `Source ${id}`,
    url: `https://${id}.example`,
    category: "general",
    language: "en",
    country,
  },
});

const item = (title: string, isoDate: string, link: string) => ({
  title,
  link,
  contentSnippet: `Description for ${title}`,
  isoDate,
});

const urlsOf = (feeds: RSSFeed[]) => feeds.map((entry) => entry.url);

describe("selectRegionFeeds", () => {
  it("rotates by country instead of taking a prefix of the feed table", () => {
    // jp has three feeds and sits first in the table, br has two, ke has one.
    const feeds = [
      feed("jp-1", "jp"),
      feed("jp-2", "jp"),
      feed("jp-3", "jp"),
      feed("br-1", "br"),
      feed("br-2", "br"),
      feed("ke-1", "ke"),
    ];

    const selected = selectRegionFeeds("south-america", 6, feeds);
    // south-america includes br but not jp/ke, so only br is eligible here.
    expect(selected).toHaveLength(2);
    expect(urlsOf(selected)).toEqual(["https://feeds.example/br-1", "https://feeds.example/br-2"]);
  });

  it("covers every country in the first round before adding a second feed", () => {
    const feeds = [
      feed("jp-1", "jp"),
      feed("jp-2", "jp"),
      feed("in-1", "in"),
      feed("in-2", "in"),
      feed("kr-1", "kr"),
    ];

    const selected = selectRegionFeeds("asia", 5, feeds);
    expect(selected.map((entry) => entry.source.id)).toEqual([
      "jp-1",
      "in-1",
      "kr-1",
      "jp-2",
      "in-2",
    ]);
  });

  it("stops at the requested source count and is deterministic", () => {
    const first = selectRegionFeeds("asia", 4);
    const second = selectRegionFeeds("asia", 4);

    expect(first).toHaveLength(4);
    expect(urlsOf(first)).toEqual(urlsOf(second));
    expect(new Set(first.map((entry) => entry.source.country)).size).toBe(4);
  });

  it("gives later countries slots within the real source table", () => {
    const selected = selectRegionFeeds("asia");

    expect(selected).toHaveLength(REGION_MAX_SOURCES);
    const countries = new Set(selected.map((entry) => entry.source.country));
    // The table starts with six Japanese and ten Indian feeds; a plain
    // slice(0, 20) only reaches five countries. Round-robin covers far more.
    expect(countries.size).toBeGreaterThanOrEqual(15);
    expect(countries.has("jp")).toBe(true);
    expect(countries.has("kr")).toBe(true);
    expect(countries.has("bt")).toBe(true);
  });

  it("returns no feeds for an unknown region", () => {
    expect(selectRegionFeeds("not-a-region")).toEqual([]);
  });
});

describe("RSSAggregator.fetchByRegion", () => {
  const asiaFeeds = [
    feed("jp-1", "jp"),
    feed("jp-2", "jp"),
    feed("in-1", "in"),
    feed("in-2", "in"),
    feed("kr-1", "kr"),
  ];

  const responses: Record<string, ReturnType<typeof item>[] | Error> = {
    "https://feeds.example/jp-1": [
      item("Japan story older", "2026-08-30T08:00:00Z", "https://example.com/jp-older"),
      item("Shared headline", "2026-08-30T12:00:00Z", "https://example.com/shared-jp"),
    ],
    "https://feeds.example/jp-2": new Error("socket hang up"),
    "https://feeds.example/in-1": [
      item("Shared headline", "2026-08-30T12:00:00Z", "https://example.com/shared-in"),
      item("India story", "2026-08-30T10:00:00Z", "https://example.com/in"),
    ],
    "https://feeds.example/in-2": new Error("404 Not Found"),
    "https://feeds.example/kr-1": [
      item("Korea story newest", "2026-08-30T14:00:00Z", "https://example.com/kr"),
    ],
  };

  beforeEach(() => {
    globalCache.clear();
    mockParseURL.mockReset();
    // Expected source failures are reported through stats; keep test output clean.
    jest.spyOn(console, "error").mockImplementation(() => undefined);
    mockParseURL.mockImplementation(async (url: string) => {
      const response = responses[url];
      if (response instanceof Error) throw response;
      return { items: response ?? [] };
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("isolates failures and reports stats for the attempted sources", async () => {
    const aggregator = new RSSAggregator();
    const result = await aggregator.fetchByRegion("asia", 10, asiaFeeds);

    expect(result.stats).toEqual({
      attemptedSources: 5,
      successfulSources: 3,
      failedSources: 2,
      coveredCountries: ["jp", "in", "kr"],
      failures: [
        { sourceId: "jp-2", error: "socket hang up" },
        { sourceId: "in-2", error: "404 Not Found" },
      ],
    });
  });

  it("sorts by publish time descending, dedupes titles and respects limit", async () => {
    const aggregator = new RSSAggregator();
    const result = await aggregator.fetchByRegion("asia", 3, asiaFeeds);

    expect(result.articles.map((article) => article.title)).toEqual([
      "Korea story newest",
      "Shared headline",
      "India story",
    ]);
    expect(result.articles[1].source.id).toBe("jp-1");
    expect(result.stats.coveredCountries).toEqual(["jp", "in", "kr"]);
  });

  it("returns an empty list but accurate stats when every source fails", async () => {
    mockParseURL.mockImplementation(async () => {
      throw new Error("network unreachable");
    });

    const aggregator = new RSSAggregator();
    const result = await aggregator.fetchByRegion("asia", 10, asiaFeeds);

    expect(result.articles).toEqual([]);
    expect(result.stats.attemptedSources).toBe(5);
    expect(result.stats.successfulSources).toBe(0);
    expect(result.stats.failedSources).toBe(5);
    expect(result.stats.coveredCountries).toEqual([]);
    expect(result.stats.failures.map((failure) => failure.sourceId)).toEqual([
      "jp-1",
      "jp-2",
      "in-1",
      "in-2",
      "kr-1",
    ]);
    expect(result.stats.failures.every((failure) => failure.error === "network unreachable")).toBe(true);
  });

  it("serves cached region results with the same stats", async () => {
    const feeds = selectRegionFeeds("asia");
    const failingUrls = new Set([feeds[0].url, feeds[1].url]);
    mockParseURL.mockImplementation(async (url: string) => {
      if (failingUrls.has(url)) throw new Error("upstream timeout");
      return { items: [item(`Story from ${url}`, "2026-08-30T08:00:00Z", `${url}/item`)] };
    });

    const aggregator = new RSSAggregator();
    const first = await aggregator.fetchByRegion("asia", 10);
    expect(first.stats.attemptedSources).toBe(feeds.length);
    expect(first.stats.failedSources).toBe(2);

    const callsAfterFirst = mockParseURL.mock.calls.length;
    const second = await aggregator.fetchByRegion("asia", 10);

    expect(mockParseURL.mock.calls.length).toBe(callsAfterFirst);
    expect(second).toBe(first);
    expect(second.stats).toEqual(first.stats);
    expect(second.stats.failedSources).toBe(2);
    expect(second.stats.failures.map((failure) => failure.error)).toEqual([
      "upstream timeout",
      "upstream timeout",
    ]);
  });

  it("does not let an injected feed list leak into the region cache", async () => {
    const aggregator = new RSSAggregator();
    await aggregator.fetchByRegion("europe", 5, [feed("local", "de")]);

    const result = await aggregator.fetchByRegion("europe", 5);
    expect(result.stats.attemptedSources).toBe(REGION_MAX_SOURCES);
    expect(result.stats.attemptedSources).toBeGreaterThan(1);
  });
});

describe("newsAggregator.getNewsByRegion", () => {
  const article = (id: string, country: string, title: string, publishedAt: string): NewsArticle => ({
    id,
    title,
    description: `Description for ${title}`,
    url: `https://example.com/${id}`,
    imageUrl: "https://example.com/image.jpg",
    source: {
      id: `source-${id}`,
      name: `Source ${id}`,
      url: "https://source.example",
      category: "general",
      language: "en",
      country,
    },
    publishedAt,
    category: "general",
    language: "en",
    country,
  });

  afterEach(() => {
    jest.restoreAllMocks();
    globalCache.clear();
  });

  it("returns region articles together with fetch stats", async () => {
    const spy = jest.spyOn(rssAggregator, "fetchByRegion").mockResolvedValue({
      articles: [
        article("a1", "jp", "Japan story", "2026-08-30T09:00:00Z"),
        article("a2", "kr", "Korea story", "2026-08-30T11:00:00Z"),
        // Removed by cleanArticles (invalid URL); must not appear in coverage.
        { ...article("a3", "cn", "China story", "2026-08-30T10:00:00Z"), url: "not-a-url" },
      ],
      stats: {
        attemptedSources: 6,
        successfulSources: 4,
        failedSources: 2,
        coveredCountries: ["jp", "kr", "cn"],
        failures: [
          { sourceId: "cn-1", error: "timeout" },
          { sourceId: "cn-2", error: "ECONNRESET" },
        ],
      },
    } satisfies RegionFetchResult);

    const result = await newsAggregator.getNewsByRegion("asia", 1, 10);

    expect(spy).toHaveBeenCalledWith("asia", 10);
    expect(result.page).toBe(1);
    expect(result.limit).toBe(10);
    expect(result.articles.map((entry) => entry.id)).toEqual(["a2", "a1"]);
    expect(result.stats.coveredCountries).toEqual(["kr", "jp"]);
    expect(result.stats.failures).toEqual([
      { sourceId: "cn-1", error: "timeout" },
      { sourceId: "cn-2", error: "ECONNRESET" },
    ]);
  });

  it("keeps failure counts when no articles survive", async () => {
    jest.spyOn(rssAggregator, "fetchByRegion").mockResolvedValue({
      articles: [],
      stats: {
        attemptedSources: 3,
        successfulSources: 0,
        failedSources: 3,
        coveredCountries: [],
        failures: [
          { sourceId: "a", error: "boom" },
          { sourceId: "b", error: "boom" },
          { sourceId: "c", error: "boom" },
        ],
      },
    });

    const result = await newsAggregator.getNewsByRegion("africa", 1, 10);
    expect(result.articles).toEqual([]);
    expect(result.stats.failedSources).toBe(3);
    expect(result.stats.successfulSources).toBe(0);
  });
});

describe("real source table sanity", () => {
  it("region feeds stay a subset of ALL_RSS_FEEDS", () => {
    const allUrls = new Set(urlsOf(ALL_RSS_FEEDS));
    for (const region of ["asia", "europe", "africa", "north-america", "south-america", "middle-east", "oceania"]) {
      for (const selected of selectRegionFeeds(region)) {
        expect(allUrls.has(selected.url)).toBe(true);
      }
    }
  });
});
