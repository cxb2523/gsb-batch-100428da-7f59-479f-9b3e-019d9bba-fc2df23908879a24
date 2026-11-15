import { describe, it, expect, vi, afterEach } from "vitest";
import type { Request } from "express";
import { parseConfig, scrapeUrl, configSchema, scrapeToolInputSchema } from "../index.js";
import { z } from "zod";

// Helper to create a minimal mock Request with query params
function mockRequest(query: Record<string, unknown> = {}): Request {
  return { query } as unknown as Request;
}

// Helper to encode a config object to base64 JSON (as Smithery sends it)
function encodeConfig(obj: unknown): string {
  return Buffer.from(JSON.stringify(obj)).toString("base64");
}

// ─── parseConfig ────────────────────────────────────────────────────────────

describe("parseConfig", () => {
  it("returns an empty object when no config query param is present", () => {
    expect(parseConfig(mockRequest())).toEqual({});
  });

  it("decodes a valid base64-encoded JSON object", () => {
    const req = mockRequest({ config: encodeConfig({ scrapiApiKey: "test-key" }) });
    expect(parseConfig(req)).toEqual({ scrapiApiKey: "test-key" });
  });

  it("returns an empty object for malformed base64", () => {
    const req = mockRequest({ config: "!!!not-valid-base64!!!" });
    expect(parseConfig(req)).toEqual({});
  });

  it("returns an empty object when the decoded value is not JSON", () => {
    const req = mockRequest({ config: Buffer.from("not json").toString("base64") });
    expect(parseConfig(req)).toEqual({});
  });

  it("returns an empty object when the decoded JSON is an array (not an object)", () => {
    const req = mockRequest({ config: encodeConfig([1, 2, 3]) });
    expect(parseConfig(req)).toEqual({});
  });

  it("returns an empty object when config param is an array (multi-value query param)", () => {
    const req = mockRequest({ config: ["a", "b"] });
    expect(parseConfig(req)).toEqual({});
  });

  it("returns an empty object when config param is null", () => {
    const req = mockRequest({ config: null });
    expect(parseConfig(req)).toEqual({});
  });
});

// ─── configSchema ────────────────────────────────────────────────────────────

describe("configSchema", () => {
  it("accepts an object with a string scrapiApiKey", () => {
    expect(() => configSchema.parse({ scrapiApiKey: "abc" })).not.toThrow();
  });

  it("accepts an object with no scrapiApiKey (optional field)", () => {
    expect(() => configSchema.parse({})).not.toThrow();
  });

  it("rejects when scrapiApiKey is not a string", () => {
    expect(() => configSchema.parse({ scrapiApiKey: 123 })).toThrow();
  });
});

// ─── scrapeToolInputSchema ──────────────────────────────────────────────────

describe("scrapeToolInputSchema", () => {
  const schema = z.object(scrapeToolInputSchema);

  it("accepts a valid URL with no browserCommands", () => {
    const parsed = schema.safeParse({ url: "https://example.com" });
    expect(parsed.success).toBe(true);
  });

  it("rejects an invalid URL", () => {
    const parsed = schema.safeParse({ url: "not-a-url" });
    expect(parsed.success).toBe(false);
    if (!parsed.success) {
      expect(parsed.error.issues[0]?.message).toContain("Invalid URL");
    }
  });

  it("rejects non-string browserCommands", () => {
    const parsed = schema.safeParse({ url: "https://example.com", browserCommands: 123 });
    expect(parsed.success).toBe(false);
  });
});

// ─── scrapeUrl ───────────────────────────────────────────────────────────────

describe("scrapeUrl", () => {
  const TEST_KEY = "test-api-key";

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns content with HTML mime type on a successful HTML response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        text: async () => "<html><body>Hello</body></html>",
      }),
    );

    const result = await scrapeUrl("https://example.com", "HTML", TEST_KEY);

    expect(result.isError).toBeFalsy();
    expect(result.content[0]).toMatchObject({
      type: "text",
      text: "<html><body>Hello</body></html>",
    });
  });

  it("returns content with Markdown mime type on a successful Markdown response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        text: async () => "# Hello",
      }),
    );

    const result = await scrapeUrl("https://example.com", "Markdown", TEST_KEY);

    expect(result.isError).toBeFalsy();
    expect(result.content[0]).toMatchObject({ type: "text", text: "# Hello" });
  });

  it("returns isError: true on a non-ok HTTP response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        text: async () => "Internal Server Error",
      }),
    );

    const result = await scrapeUrl("https://example.com", "HTML", TEST_KEY);

    expect(result.isError).toBe(true);
    expect(result.content[0].text).toContain("Internal Server Error");
  });

  it("returns isError: true when fetch throws (network error)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Network failure")));

    const result = await scrapeUrl("https://example.com", "HTML", TEST_KEY);

    expect(result.isError).toBe(true);
    expect(result.content[0].text).toContain("Network failure");
  });

  it("sends the correct API key header", async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, text: async () => "" });
    vi.stubGlobal("fetch", mockFetch);

    await scrapeUrl("https://example.com", "HTML", "my-secret-key");

    const calledHeaders = mockFetch.mock.calls[0][1].headers as Record<string, string>;
    expect(calledHeaders["X-API-KEY"]).toBe("my-secret-key");
  });

  it("includes parsed browserCommands in the request body", async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, text: async () => "" });
    vi.stubGlobal("fetch", mockFetch);

    const cmds = JSON.stringify([{ click: "#btn" }, { wait: 1000 }]);
    await scrapeUrl("https://example.com", "HTML", TEST_KEY, cmds);

    const sentBody = JSON.parse(mockFetch.mock.calls[0][1].body as string) as {
      browserCommands: unknown[];
    };
    expect(sentBody.browserCommands).toEqual([{ click: "#btn" }, { wait: 1000 }]);
  });

  it("returns isError: true when browserCommands is not a JSON array", async () => {
    const result = await scrapeUrl(
      "https://example.com",
      "HTML",
      TEST_KEY,
      JSON.stringify({ click: "#btn" }), // object, not array
    );

    expect(result.isError).toBe(true);
    expect(result.content[0].text).toContain("Browser commands must be a JSON array");
  });

  it("returns isError: true when browserCommands is invalid JSON", async () => {
    const result = await scrapeUrl("https://example.com", "HTML", TEST_KEY, "not-json");

    expect(result.isError).toBe(true);
    expect(result.content[0].text).toContain("Invalid browser commands format");
  });

  it("ignores whitespace-only browserCommands", async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, text: async () => "" });
    vi.stubGlobal("fetch", mockFetch);

    await scrapeUrl("https://example.com", "HTML", TEST_KEY, "   ");

    const sentBody = JSON.parse(mockFetch.mock.calls[0][1].body as string) as {
      browserCommands?: unknown;
    };
    expect(sentBody.browserCommands).toBeUndefined();
  });

  it("ignores empty-string browserCommands", async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, text: async () => "" });
    vi.stubGlobal("fetch", mockFetch);

    await scrapeUrl("https://example.com", "HTML", TEST_KEY, "");

    const sentBody = JSON.parse(mockFetch.mock.calls[0][1].body as string) as {
      browserCommands?: unknown;
    };
    expect(sentBody.browserCommands).toBeUndefined();
  });
});

// ─── scrapeUrl browser command validation ───────────────────────────────────

describe("scrapeUrl browser command validation", () => {
  const TEST_KEY = "test-api-key";

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function stubFetch() {
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, text: async () => "" });
    vi.stubGlobal("fetch", mockFetch);
    return mockFetch;
  }

  it("rejects a wait command that is not a number and does not call fetch", async () => {
    const mockFetch = stubFetch();

    const result = await scrapeUrl(
      "https://example.com",
      "HTML",
      TEST_KEY,
      JSON.stringify([{ wait: "soon" }]),
    );

    expect(result.isError).toBe(true);
    expect(result.content[0].text).toContain("command #1");
    expect(result.content[0].text).toContain('"wait"');
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("rejects a wait command exceeding 15000 ms and reports the command position", async () => {
    const mockFetch = stubFetch();

    const result = await scrapeUrl(
      "https://example.com",
      "HTML",
      TEST_KEY,
      JSON.stringify([{ click: "#btn" }, { wait: 20000 }]),
    );

    expect(result.isError).toBe(true);
    expect(result.content[0].text).toContain("command #2");
    expect(result.content[0].text).toContain('"wait"');
    expect(result.content[0].text).toContain("15000");
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("rejects a scroll command that is not a number", async () => {
    const mockFetch = stubFetch();

    const result = await scrapeUrl(
      "https://example.com",
      "HTML",
      TEST_KEY,
      JSON.stringify([{ scroll: "1000" }]),
    );

    expect(result.isError).toBe(true);
    expect(result.content[0].text).toContain("command #1");
    expect(result.content[0].text).toContain('"scroll"');
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("rejects a command object without any known action key", async () => {
    const mockFetch = stubFetch();

    const result = await scrapeUrl(
      "https://example.com",
      "HTML",
      TEST_KEY,
      JSON.stringify([{ hover: "#btn" }]),
    );

    expect(result.isError).toBe(true);
    expect(result.content[0].text).toContain("command #1");
    expect(result.content[0].text).toContain("no known action key");
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("rejects a command that is not an object", async () => {
    const mockFetch = stubFetch();

    const result = await scrapeUrl("https://example.com", "HTML", TEST_KEY, JSON.stringify(["wait"]));

    expect(result.isError).toBe(true);
    expect(result.content[0].text).toContain("command #1");
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("sends valid commands unchanged, including wait at the 15000 ms boundary", async () => {
    const mockFetch = stubFetch();

    const commands = [
      { click: "#accept" },
      { wait: 15000 },
      { scroll: -500 },
      { input: { "input[name='q']": "test" } },
      { select: { "select[name='c']": "USA" } },
      { waitfor: "#result" },
      { javascript: "console.log('x')" },
    ];
    await scrapeUrl("https://example.com", "HTML", TEST_KEY, JSON.stringify(commands));

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const sentBody = JSON.parse(mockFetch.mock.calls[0][1].body as string) as {
      browserCommands: unknown[];
    };
    expect(sentBody.browserCommands).toEqual(commands);
  });
});

// ─── scrapeUrl upstream failure classification ──────────────────────────────

describe("scrapeUrl upstream failure classification", () => {
  const TEST_KEY = "test-api-key";

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function stubHttpError(status: number, body: string) {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status,
      text: async () => body,
    });
    vi.stubGlobal("fetch", mockFetch);
    return mockFetch;
  }

  it.each([401, 403])("classifies HTTP %i as an authentication failure", async (status) => {
    stubHttpError(status, "Invalid API key");

    const result = await scrapeUrl("https://example.com", "HTML", TEST_KEY);

    expect(result.isError).toBe(true);
    expect(result.content[0].text).toContain("authentication failed");
    expect(result.content[0].text).toContain(String(status));
    expect(result.content[0].text).toContain("Invalid API key");
  });

  it.each([400, 422])("classifies non-auth HTTP %i as rejected parameters", async (status) => {
    stubHttpError(status, "Unknown browser command");

    const result = await scrapeUrl("https://example.com", "HTML", TEST_KEY);

    expect(result.isError).toBe(true);
    expect(result.content[0].text).toContain("invalid parameters");
    expect(result.content[0].text).not.toContain("authentication failed");
    expect(result.content[0].text).toContain("Unknown browser command");
  });

  it("classifies HTTP 5xx as an upstream server error", async () => {
    stubHttpError(503, "Service Unavailable");

    const result = await scrapeUrl("https://example.com", "HTML", TEST_KEY);

    expect(result.isError).toBe(true);
    expect(result.content[0].text).toContain("server error");
    expect(result.content[0].text).toContain("503");
  });

  it("classifies an AbortSignal timeout as a timeout", async () => {
    const timeoutError = new Error("The operation timed out");
    timeoutError.name = "TimeoutError";
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(timeoutError));

    const result = await scrapeUrl("https://example.com", "HTML", TEST_KEY);

    expect(result.isError).toBe(true);
    expect(result.content[0].text).toContain("timed out");
    expect(result.content[0].text).not.toContain("Network error");
  });

  it("classifies an AbortError as a timeout", async () => {
    const abortError = new Error("This operation was aborted");
    abortError.name = "AbortError";
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abortError));

    const result = await scrapeUrl("https://example.com", "HTML", TEST_KEY);

    expect(result.isError).toBe(true);
    expect(result.content[0].text).toContain("timed out");
  });

  it("classifies a generic fetch rejection as a network error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    const result = await scrapeUrl("https://example.com", "HTML", TEST_KEY);

    expect(result.isError).toBe(true);
    expect(result.content[0].text).toContain("Network error");
    expect(result.content[0].text).toContain("fetch failed");
  });
});

// ─── scrapeUrl success path ─────────────────────────────────────────────────

describe("scrapeUrl success path", () => {
  const TEST_KEY = "test-api-key";

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the upstream body with the HTML mime type unchanged", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        text: async () => "<html><body>Hi</body></html>",
      }),
    );

    const result = await scrapeUrl("https://example.com", "HTML", TEST_KEY);

    expect(result.isError).toBeFalsy();
    expect(result.content[0]).toMatchObject({
      type: "text",
      text: "<html><body>Hi</body></html>",
      _meta: { mimeType: "text/html" },
    });
  });

  it("returns the upstream body with the Markdown mime type unchanged", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        text: async () => "# Title",
      }),
    );

    const result = await scrapeUrl("https://example.com", "Markdown", TEST_KEY);

    expect(result.isError).toBeFalsy();
    expect(result.content[0]).toMatchObject({
      type: "text",
      text: "# Title",
      _meta: { mimeType: "text/markdown" },
    });
  });
});
