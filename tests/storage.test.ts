import "fake-indexeddb/auto";
import { describe, it, expect, beforeEach } from "vitest";
import {
  savePages,
  getAllPages,
  getPagesBySourceUrl,
  clearAll,
  type StoredPage,
} from "@/lib/storage";
import { runCrawl, type CrawlPage } from "@/lib/crawl";
import { encode, mockFetchResponse } from "./helpers/mock-stream";

beforeEach(async () => {
  await clearAll();
});

const pageLine = (url: string, content: string) =>
  JSON.stringify({ url, content, status: 200 });

describe("savePages — source-site attribution", () => {
  it("writes each page's source site into IndexedDB", async () => {
    const pages: CrawlPage[] = [
      {
        url: "https://a.example/blog/post-1",
        content: "<html>a1</html>",
        sourceUrl: "https://a.example",
      },
      {
        url: "https://b.example/about",
        content: "<html>b</html>",
        sourceUrl: "https://b.example",
      },
    ];

    await savePages(pages);
    const stored = await getAllPages();

    expect(stored).toHaveLength(2);
    const byUrl = new Map(stored.map((p) => [p.url, p]));
    expect(byUrl.get("https://a.example/blog/post-1")?.sourceUrl).toBe(
      "https://a.example"
    );
    expect(byUrl.get("https://b.example/about")?.sourceUrl).toBe(
      "https://b.example"
    );
  });

  it("can look pages back up through the sourceUrl index", async () => {
    await savePages([
      {
        url: "https://a.example/1",
        content: "one",
        sourceUrl: "https://a.example",
      },
      {
        url: "https://a.example/2",
        content: "two",
        sourceUrl: "https://a.example",
      },
      {
        url: "https://b.example/1",
        content: "three",
        sourceUrl: "https://b.example",
      },
    ]);

    const fromA = await getPagesBySourceUrl("https://a.example");
    expect(fromA.map((p) => p.url).sort()).toEqual([
      "https://a.example/1",
      "https://a.example/2",
    ]);
  });
});

describe("savePages — duplicate URLs overwrite rather than accumulate", () => {
  it("replaces the stored record when the same page URL is saved again", async () => {
    await savePages([
      { url: "https://a.example/p", content: "old", sourceUrl: "https://a.example" },
    ]);
    await savePages([
      { url: "https://a.example/p", content: "new", sourceUrl: "https://a.example" },
    ]);

    const stored = await getAllPages();
    expect(stored).toHaveLength(1);
    expect(stored[0].content).toBe("new");
  });
});

describe("interrupted crawl — received pages still land in IndexedDB", () => {
  it("persists exactly the pages received before reader.read() threw", async () => {
    const inputUrls = ["https://a.example", "https://b.example"];
    const firstLines = [
      pageLine("https://a.example/1", "first"),
      pageLine("https://b.example/2", "second"),
    ].join("\n");

    const result = await runCrawl({
      apiUrl: "https://api.spider.cloud",
      body: { url: inputUrls.join(",") },
      headers: { "content-type": "application/jsonl" },
      inputUrls,
      fetchImpl: mockFetchResponse({
        chunks: [encode(firstLines + "\n")],
        readError: new Error("stream aborted"),
        throwAfterChunks: 1,
      }),
    });

    expect(result.outcome).toBe("interrupted");
    expect(result.receivedCount).toBe(2);

    // The UI persists whatever runCrawl returned even on interruption.
    await savePages(result.pages);
    const stored: StoredPage[] = await getAllPages();

    expect(stored).toHaveLength(2);
    const byUrl = new Map(stored.map((p) => [p.url, p]));
    expect(byUrl.get("https://a.example/1")?.sourceUrl).toBe(
      "https://a.example"
    );
    expect(byUrl.get("https://b.example/2")?.sourceUrl).toBe(
      "https://b.example"
    );
  });

  it("does not write anything when the crawl failed with zero pages", async () => {
    const result = await runCrawl({
      apiUrl: "https://api.spider.cloud",
      body: { url: "https://a.example" },
      headers: {},
      inputUrls: ["https://a.example"],
      fetchImpl: (async () => {
        throw new TypeError("Failed to fetch");
      }) as unknown as typeof fetch,
    });

    expect(result.outcome).toBe("failed");
    await savePages(result.pages); // no-op for an empty list
    expect(await getAllPages()).toEqual([]);
  });
});
