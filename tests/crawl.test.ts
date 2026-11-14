import { describe, it, expect } from "vitest";
import {
  runCrawl,
  parseUrlList,
  matchSourceUrl,
  feedChunk,
  flushBuffer,
  createStreamBuffer,
  parseJsonlLine,
  type CrawlPage,
} from "@/lib/crawl";
import {
  encode,
  splitIntoChunks,
  mockFetchResponse,
} from "./helpers/mock-stream";

const baseParams = {
  apiUrl: "https://api.spider.cloud",
  body: { url: "https://a.example,https://b.example" },
  headers: { "content-type": "application/jsonl" },
  inputUrls: ["https://a.example", "https://b.example"],
};

const pageLine = (url: string, content: string, status = 200) =>
  JSON.stringify({ url, content, status });

describe("parseUrlList", () => {
  it("splits comma-separated input and normalizes schemes", () => {
    expect(parseUrlList("a.example, https://b.example/path, c.example/x")).toEqual([
      "https://a.example",
      "https://b.example/path",
      "https://c.example/x",
    ]);
  });

  it("drops blank entries", () => {
    expect(parseUrlList(" a.example , ,b.example,")).toEqual([
      "https://a.example",
      "https://b.example",
    ]);
  });
});

describe("matchSourceUrl", () => {
  const inputs = [
    "https://a.example/blog",
    "https://a.example/docs",
    "https://b.example",
  ];

  it("attributes same-host pages by longest matching path prefix", () => {
    expect(matchSourceUrl("https://a.example/blog/post-1", inputs)).toBe(
      "https://a.example/blog"
    );
    expect(matchSourceUrl("https://a.example/docs/intro", inputs)).toBe(
      "https://a.example/docs"
    );
  });

  it("attributes subdomain pages to the matching input host", () => {
    expect(matchSourceUrl("https://www.b.example/x", inputs)).toBe(
      "https://b.example"
    );
    expect(matchSourceUrl("https://sub.b.example/x", inputs)).toBe(
      "https://b.example"
    );
  });

  it("uses the sole input for every page", () => {
    expect(
      matchSourceUrl("https://anything.example/x", ["https://root.example"])
    ).toBe("https://root.example");
  });

  it("falls back to the first input for unmatchable URLs", () => {
    expect(matchSourceUrl("not a url", inputs)).toBe(inputs[0]);
  });
});

describe("cross-chunk buffering", () => {
  it("reassembles a JSONL line split across multiple chunks", () => {
    const buffer = createStreamBuffer();
    const full = pageLine("https://a.example/p", "<html>hi</html>") + "\n";
    const chunks = splitIntoChunks(full, 7); // tiny slices guarantee splits
    const lines: string[] = [];
    for (const chunk of chunks) {
      lines.push(
        ...feedChunk(buffer, new TextDecoder().decode(chunk, { stream: true }))
      );
    }
    const tail = flushBuffer(buffer);
    if (tail) lines.push(tail);
    expect(lines).toHaveLength(1);
    expect(parseJsonlLine(lines[0])?.url).toBe("https://a.example/p");
  });

  it("holds an incomplete final line in the buffer until flush", () => {
    const buffer = createStreamBuffer();
    const partial = JSON.stringify({ url: "https://a.example/x", content: "c" });
    const lines = feedChunk(buffer, partial); // no trailing newline
    expect(lines).toEqual([]);
    expect(buffer.current).toBe(partial);
    expect(flushBuffer(buffer)).toBe(partial);
  });
});

describe("runCrawl — streaming outcomes", () => {
  it("parses normal lines and marks a clean stream as completed", async () => {
    const payload = [
      pageLine("https://a.example/", "A home"),
      pageLine("https://b.example/", "B home"),
      "",
    ].join("\n");
    const seen: CrawlPage[][] = [];

    const result = await runCrawl({
      ...baseParams,
      fetchImpl: mockFetchResponse({ chunks: [encode(payload)] }),
      onPage: (pages) => seen.push([...pages]),
    });

    expect(result.outcome).toBe("completed");
    expect(result.receivedCount).toBe(2);
    expect(result.pages.map((p) => p.url)).toEqual([
      "https://a.example/",
      "https://b.example/",
    ]);
    // onPage fires once per valid line.
    expect(seen).toHaveLength(2);
  });

  it("reassembles truncated lines arriving across chunks", async () => {
    const payload = [
      pageLine("https://a.example/1", "one"),
      pageLine("https://a.example/2", "two"),
    ].join("\n");
    const chunks = splitIntoChunks(payload, 13);

    const result = await runCrawl({
      ...baseParams,
      fetchImpl: mockFetchResponse({ chunks }),
    });

    expect(result.outcome).toBe("completed");
    expect(result.pages.map((p) => p.url)).toEqual([
      "https://a.example/1",
      "https://a.example/2",
    ]);
  });

  it("flushes a final line without a trailing newline when done", async () => {
    const payload = pageLine("https://a.example/tail", "tail");
    const result = await runCrawl({
      ...baseParams,
      fetchImpl: mockFetchResponse({ chunks: [encode(payload)] }),
    });
    expect(result.outcome).toBe("completed");
    expect(result.pages).toHaveLength(1);
    expect(result.pages[0].content).toBe("tail");
  });

  it("keeps already-received pages and marks the run interrupted when reader.read() throws", async () => {
    const firstLines = [
      pageLine("https://a.example/1", "first"),
      pageLine("https://a.example/2", "second"),
    ].join("\n");
    const boom = new Error("network socket closed mid-stream");

    const result = await runCrawl({
      ...baseParams,
      fetchImpl: mockFetchResponse({
        chunks: [encode(firstLines + "\n"), encode(pageLine("https://x", "z"))],
        readError: boom,
        throwAfterChunks: 1,
      }),
    });

    expect(result.outcome).toBe("interrupted");
    expect(result.error).toBe(boom.message);
    // The two pages received before the throw survive untouched.
    expect(result.receivedCount).toBe(2);
    expect(result.pages.map((p) => p.url)).toEqual([
      "https://a.example/1",
      "https://a.example/2",
    ]);
  });

  it("marks the run failed (not interrupted) when the error occurs before any page arrives", async () => {
    const result = await runCrawl({
      ...baseParams,
      fetchImpl: mockFetchResponse({
        chunks: [encode(pageLine("https://x", "z"))],
        readError: new Error("connection reset"),
        throwAfterChunks: 0,
      }),
    });

    expect(result.outcome).toBe("failed");
    expect(result.receivedCount).toBe(0);
    expect(result.pages).toEqual([]);
    expect(result.error).toContain("connection reset");
  });

  it("returns failed when fetch itself rejects", async () => {
    const result = await runCrawl({
      ...baseParams,
      fetchImpl: (async () => {
        throw new TypeError("Failed to fetch");
      }) as unknown as typeof fetch,
    });

    expect(result.outcome).toBe("failed");
    expect(result.receivedCount).toBe(0);
    expect(result.error).toContain("Failed to fetch");
  });

  it("returns failed with the HTTP status when the response is not ok", async () => {
    const result = await runCrawl({
      ...baseParams,
      fetchImpl: mockFetchResponse({
        chunks: [],
        ok: false,
        status: 401,
        statusText: "Unauthorized",
      }),
    });

    expect(result.outcome).toBe("failed");
    expect(result.error).toContain("401");
  });

  it("overwrites a repeated page URL with the newest content instead of stacking duplicates", async () => {
    const payload = [
      pageLine("https://a.example/same", "version-1"),
      pageLine("https://a.example/same", "version-2"),
    ].join("\n");

    const result = await runCrawl({
      ...baseParams,
      fetchImpl: mockFetchResponse({ chunks: [encode(payload)] }),
    });

    expect(result.receivedCount).toBe(1);
    expect(result.pages).toHaveLength(1);
    expect(result.pages[0].content).toBe("version-2");
  });

  it("ignores malformed lines and records without a url", async () => {
    const payload = [
      "not-json-at-all",
      JSON.stringify({ content: "no url here" }),
      pageLine("https://a.example/ok", "fine"),
    ].join("\n");

    const result = await runCrawl({
      ...baseParams,
      fetchImpl: mockFetchResponse({ chunks: [encode(payload)] }),
    });

    expect(result.receivedCount).toBe(1);
    expect(result.pages[0].url).toBe("https://a.example/ok");
  });
});

describe("runCrawl — source-site attribution", () => {
  it("stamps every page with the user-entered site it came from", async () => {
    const payload = [
      pageLine("https://a.example/blog/post-1", "p1"),
      pageLine("https://a.example/blog/post-2", "p2"),
      pageLine("https://b.example/about", "about"),
    ].join("\n");

    const result = await runCrawl({
      ...baseParams,
      fetchImpl: mockFetchResponse({ chunks: [encode(payload)] }),
    });

    expect(result.pages.map((p) => p.sourceUrl)).toEqual([
      "https://a.example",
      "https://a.example",
      "https://b.example",
    ]);
  });

  it("attributes pages when two inputs share a hostname but differ in path", async () => {
    const inputUrls = ["https://a.example/blog", "https://a.example/docs"];
    const payload = [
      pageLine("https://a.example/blog/x", "bx"),
      pageLine("https://a.example/docs/y", "dy"),
    ].join("\n");

    const result = await runCrawl({
      ...baseParams,
      body: { url: inputUrls.join(",") },
      inputUrls,
      fetchImpl: mockFetchResponse({ chunks: [encode(payload)] }),
    });

    expect(result.pages.map((p) => p.sourceUrl)).toEqual([
      "https://a.example/blog",
      "https://a.example/docs",
    ]);
  });
});
