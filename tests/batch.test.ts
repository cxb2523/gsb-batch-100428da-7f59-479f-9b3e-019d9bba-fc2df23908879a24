import { afterEach, beforeEach, describe, expect, spyOn, test } from "bun:test";
import { ScrapeGraphAI, batchScrape, scrape } from "../src/scrapegraphai.js";

const API_KEY = "test-sgai-key";

function json(body: unknown, status = 200): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { "Content-Type": "application/json" },
	});
}

function successBody(url: string) {
	return {
		results: { markdown: { data: [`# ${url}`] } },
		metadata: { contentType: "text/html" },
	};
}

type FetchInit = RequestInit;

let fetchSpy: ReturnType<typeof spyOn<typeof globalThis, "fetch">>;

beforeEach(() => {
	// Keep retry backoff from slowing the suite down.
	process.env.SGAI_BATCH_RETRY_BASE_MS = "0";
});

afterEach(() => {
	fetchSpy?.mockRestore();
	// biome-ignore lint/performance/noDelete: tests must truly unset the env var
	delete process.env.SGAI_BATCH_RETRY_BASE_MS;
});

function requestedUrl(init: FetchInit): string {
	return (JSON.parse(init.body as string) as { url: string }).url;
}

/** Route a mocked fetch by the `url` field of the POSTed scrape body. */
function mockByUrl(handler: (url: string, attempt: number) => Promise<Response> | Response) {
	const calls = new Map<string, number>();
	fetchSpy = spyOn(globalThis, "fetch").mockImplementation(((_input: string, init: FetchInit) => {
		const url = requestedUrl(init);
		const attempt = (calls.get(url) ?? 0) + 1;
		calls.set(url, attempt);
		return Promise.resolve(handler(url, attempt));
	}) as typeof fetch);
	return calls;
}

describe("batch.scrape", () => {
	test("all urls succeed and results keep input order", async () => {
		const urls = ["https://a.example.com", "https://b.example.com", "https://c.example.com"];
		mockByUrl((url) => json(successBody(url)));

		const results = await batchScrape(API_KEY, { urls });

		expect(results).toHaveLength(3);
		for (const [i, result] of results.entries()) {
			expect(result.url).toBe(urls[i]);
			expect(result.status).toBe("success");
			expect(result.data).toEqual(successBody(urls[i]));
			expect(result.error).toBeNull();
			expect(result.attempts).toBe(1);
			expect(result.elapsedMs).toBeGreaterThanOrEqual(0);
		}
	});

	test("passes formats through to every request and defaults to none", async () => {
		const formats = [{ type: "markdown" as const, mode: "reader" as const }];
		const bodies: unknown[] = [];
		fetchSpy = spyOn(globalThis, "fetch").mockImplementation(((_input: string, init: FetchInit) => {
			bodies.push(JSON.parse(init.body as string));
			return Promise.resolve(json(successBody("https://example.com")));
		}) as typeof fetch);

		await batchScrape(API_KEY, {
			urls: ["https://a.example.com", "https://b.example.com"],
			formats,
		});
		await batchScrape(API_KEY, { urls: ["https://c.example.com"] });
		await batchScrape(API_KEY, { urls: ["https://d.example.com"], formats: ["markdown", "links"] });

		expect(bodies[0]).toEqual({ url: "https://a.example.com", formats });
		expect(bodies[1]).toEqual({ url: "https://b.example.com", formats });
		expect(bodies[2]).toEqual({ url: "https://c.example.com" });
		// Bare format names are normalized into the endpoint's format-entry shape.
		expect(bodies[3]).toEqual({
			url: "https://d.example.com",
			formats: [{ type: "markdown" }, { type: "links" }],
		});
	});

	test("retries 429 with increasing delay and keeps the eventual success", async () => {
		const flaky = "https://flaky.example.com";
		const steady = "https://steady.example.com";
		const delays: number[] = [];
		const originalSetTimeout = globalThis.setTimeout;
		// Capture backoff delays while still letting the timers fire.
		globalThis.setTimeout = ((fn: () => void, ms?: number) => {
			delays.push(ms ?? 0);
			return originalSetTimeout(fn, ms);
		}) as typeof setTimeout;
		process.env.SGAI_BATCH_RETRY_BASE_MS = "5";

		try {
			mockByUrl((url, attempt) => {
				if (url === flaky && attempt < 3) return json({}, 429);
				return json(successBody(url));
			});

			const results = await batchScrape(API_KEY, {
				urls: [flaky, steady],
			});

			const [flakyResult, steadyResult] = results;
			expect(flakyResult?.status).toBe("success");
			expect(flakyResult?.attempts).toBe(3);
			expect(flakyResult?.data).toEqual(successBody(flaky));
			expect(steadyResult?.status).toBe("success");
			expect(steadyResult?.attempts).toBe(1);
			expect(delays).toHaveLength(2);
			if (delays.length === 2) expect(delays[1]).toBeGreaterThan(delays[0] ?? 0);
		} finally {
			globalThis.setTimeout = originalSetTimeout;
		}
	});

	test("retries timeouts and network errors, then reports the last error", async () => {
		const timedOut = "https://timeout.example.com";
		const offline = "https://offline.example.com";
		mockByUrl((url, attempt) => {
			if (url === timedOut)
				return Promise.reject(new DOMException("aborted", "TimeoutError")) as never;
			if (url === offline) return Promise.reject(new TypeError("fetch failed")) as never;
			return json(successBody(url));
		});

		const results = await batchScrape(API_KEY, {
			urls: [timedOut, offline, "https://ok.example.com"],
			maxRetries: 2,
		});

		expect(results[0]?.status).toBe("error");
		expect(results[0]?.attempts).toBe(3);
		expect(results[0]?.error).toBe("Request timed out");
		expect(results[1]?.status).toBe("error");
		expect(results[1]?.attempts).toBe(3);
		expect(results[1]?.error).toBe("fetch failed");
		expect(results[2]?.status).toBe("success");
	});

	test("stops immediately on non-recoverable 4xx errors", async () => {
		const counts = mockByUrl((url) => {
			if (url === "https://unauthorized.example.com") return json({ detail: "Bad key" }, 401);
			if (url === "https://broke.example.com") return json({}, 402);
			if (url === "https://invalid.example.com") return json({}, 422);
			return json(successBody(url));
		});

		const results = await batchScrape(API_KEY, {
			urls: [
				"https://unauthorized.example.com",
				"https://broke.example.com",
				"https://invalid.example.com",
				"https://ok.example.com",
			],
		});

		expect(results.map((r) => r.status)).toEqual(["error", "error", "error", "success"]);
		for (const result of results.slice(0, 3)) {
			expect(result.attempts).toBe(1);
			expect(counts.get(result.url)).toBe(1);
		}
		expect(results[0]?.error).toContain("Invalid or missing API key");
		expect(results[0]?.error).toContain("Bad key");
		expect(results[1]?.error).toContain("Insufficient credits");
		expect(results[2]?.error).toContain("Invalid parameters");
		// Recoverable neighbours still go through the normal path.
		expect(counts.get("https://ok.example.com")).toBe(1);
	});

	test("never exceeds the concurrency limit", async () => {
		const urls = Array.from({ length: 12 }, (_, i) => `https://site-${i}.example.com`);
		let inFlight = 0;
		let peak = 0;

		fetchSpy = spyOn(globalThis, "fetch").mockImplementation(((_input: string, init: FetchInit) => {
			inFlight++;
			peak = Math.max(peak, inFlight);
			const url = requestedUrl(init);
			return new Promise<Response>((resolve) => {
				setTimeout(() => {
					inFlight--;
					resolve(json(successBody(url)));
				}, 10);
			});
		}) as typeof fetch);

		const results = await batchScrape(API_KEY, { urls, concurrency: 3 });

		expect(results).toHaveLength(12);
		expect(results.every((r) => r.status === "success")).toBe(true);
		expect(peak).toBe(3);
		expect(peak).toBeLessThanOrEqual(3);
		// Ordering is preserved even though completions interleave.
		expect(results.map((r) => r.url)).toEqual(urls);
	});

	test("concurrency defaults to 3", async () => {
		const urls = Array.from({ length: 9 }, (_, i) => `https://default-${i}.example.com`);
		let inFlight = 0;
		let peak = 0;
		fetchSpy = spyOn(globalThis, "fetch").mockImplementation(((_input: string, init: FetchInit) => {
			inFlight++;
			peak = Math.max(peak, inFlight);
			const url = requestedUrl(init);
			return new Promise<Response>((resolve) => {
				setTimeout(() => {
					inFlight--;
					resolve(json(successBody(url)));
				}, 10);
			});
		}) as typeof fetch);

		await batchScrape(API_KEY, { urls });

		expect(peak).toBe(3);
	});

	test("one failing url does not abort the rest of the batch", async () => {
		mockByUrl((url) => (url.includes("bad") ? json({}, 401) : json(successBody(url))));

		const results = await batchScrape(API_KEY, {
			urls: ["https://bad.example.com", "https://good-1.example.com", "https://good-2.example.com"],
			concurrency: 1,
		});

		expect(results[0]?.status).toBe("error");
		expect(results[1]?.status).toBe("success");
		expect(results[2]?.status).toBe("success");
	});

	test("maxRetries: 0 disables retries even for retryable failures", async () => {
		const counts = mockByUrl(() => json({}, 429));

		const results = await batchScrape(API_KEY, {
			urls: ["https://limited.example.com"],
			maxRetries: 0,
		});

		expect(results[0]?.status).toBe("error");
		expect(results[0]?.attempts).toBe(1);
		expect(results[0]?.error).toContain("Rate limited");
		expect(counts.get("https://limited.example.com")).toBe(1);
	});

	test("empty url list returns an empty result set", async () => {
		fetchSpy = spyOn(globalThis, "fetch").mockResolvedValue(
			json(successBody("https://example.com")),
		);

		const results = await batchScrape(API_KEY, { urls: [] });

		expect(results).toEqual([]);
		expect(fetchSpy.mock.calls).toHaveLength(0);
	});

	test("5xx errors are reported but not retried", async () => {
		const counts = mockByUrl(() => json({}, 500));

		const results = await batchScrape(API_KEY, {
			urls: ["https://broken.example.com"],
			maxRetries: 3,
		});

		expect(results[0]?.status).toBe("error");
		expect(results[0]?.attempts).toBe(1);
		expect(results[0]?.error).toContain("Server error");
		expect(counts.get("https://broken.example.com")).toBe(1);
	});

	test("rejects invalid concurrency and maxRetries values", async () => {
		fetchSpy = spyOn(globalThis, "fetch").mockResolvedValue(
			json(successBody("https://example.com")),
		);

		await expect(
			batchScrape(API_KEY, { urls: ["https://example.com"], concurrency: 0 }),
		).rejects.toThrow("concurrency must be a positive integer");
		await expect(
			batchScrape(API_KEY, { urls: ["https://example.com"], concurrency: 1.5 }),
		).rejects.toThrow("concurrency must be a positive integer");
		await expect(
			batchScrape(API_KEY, { urls: ["https://example.com"], maxRetries: -1 }),
		).rejects.toThrow("maxRetries must be a non-negative integer");
		expect(fetchSpy.mock.calls).toHaveLength(0);
	});
});

describe("sgai.batch.scrape", () => {
	test("is exposed on the client and shares the scrape semantics", async () => {
		const key = "client-key";
		fetchSpy = spyOn(globalThis, "fetch").mockImplementation(((_input: string, init: FetchInit) => {
			const url = requestedUrl(init);
			if (url.includes("fail")) return Promise.resolve(json({}, 402));
			return Promise.resolve(json(successBody(url)));
		}) as typeof fetch);

		const sgai = ScrapeGraphAI({ apiKey: key });
		const results = await sgai.batch.scrape({
			urls: ["https://ok.example.com", "https://fail.example.com"],
		});

		expect(results[0]?.status).toBe("success");
		expect(results[0]?.data).toEqual(successBody("https://ok.example.com"));
		expect(results[1]?.status).toBe("error");
		expect(results[1]?.error).toContain("Insufficient credits");
		expect((fetchSpy.mock.calls[0]?.[1] as FetchInit).headers).toMatchObject({
			"SGAI-APIKEY": key,
		});
	});
});

describe("single scrape is unchanged", () => {
	test("still maps HTTP errors and returns ApiResult", async () => {
		fetchSpy = spyOn(globalThis, "fetch").mockResolvedValueOnce(json({}, 401));

		const res = await scrape(API_KEY, { url: "https://example.com" });

		expect(res.status).toBe("error");
		expect(res.error).toContain("Invalid or missing API key");
		expect(res.elapsedMs).toBe(0);
	});
});
