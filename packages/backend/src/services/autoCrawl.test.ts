/**
 * Tests for AutoCrawlService scheduling logic.
 *
 * Focus areas:
 * - Concurrent interceptions of the same host start a single crawl and share
 *   the same in-flight result.
 * - A failed start, or a deleted/cleared job, makes the host retryable.
 * - Existing skip conditions (static resources, scope, HTTPQL filter, existing
 *   job) keep their original behavior.
 */

import type { CrawlJob } from "shared";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { configStore } from "../stores/configStore";
import { jobsStore } from "../stores/jobsStore";
import type { BackendSDK, InterceptedRequest } from "../types";

import { AutoCrawlService } from "./autoCrawl";

vi.mock("../stores/configStore", () => ({
  configStore: {
    getConfig: vi.fn(),
  },
}));

vi.mock("../stores/jobsStore", () => ({
  jobsStore: {
    getJobs: vi.fn(),
  },
}));

const startMock = vi.fn();

vi.mock("./crawler", () => ({
  CrawlerService: {
    start: (...args: unknown[]) => startMock(...args),
  },
}));

const matchesHttpqlFilterMock = vi.fn();

vi.mock("./crawler/httpqlFilter", () => ({
  matchesHttpqlFilter: (...args: unknown[]) => matchesHttpqlFilterMock(...args),
}));

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function makeRequest(
  overrides: Partial<InterceptedRequest> = {},
): InterceptedRequest {
  return {
    getHost: () => "example.com",
    getPath: () => "/dashboard",
    getPort: () => 443,
    getTls: () => true,
    ...overrides,
  } as unknown as InterceptedRequest;
}

function makeJob(host: string, id: string): CrawlJob {
  return {
    id,
    host,
    targetUrl: `https://${host}`,
    status: "running",
    depth: 5,
    discoveredUrls: 0,
    crawledUrls: 0,
    startedAt: new Date(),
    completedAt: undefined,
  } as CrawlJob;
}

function makeSdk(): BackendSDK {
  return {
    events: {
      onInterceptResponse: vi.fn(),
    },
    requests: {
      inScope: vi.fn().mockReturnValue(true),
    },
    api: {
      send: vi.fn(),
    },
  } as unknown as BackendSDK;
}

const okResult = (host = "example.com") => ({
  kind: "Ok" as const,
  value: { job: makeJob(host, "job-1"), jobId: "job-1" },
});

const OK_RESULT = okResult();

describe("AutoCrawlService", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    AutoCrawlService.clearSeenHosts();

    vi.mocked(configStore.getConfig).mockReturnValue({
      enabled: true,
      crawlInScopeOnly: false,
      httpqlFilter: undefined,
    } as ReturnType<typeof configStore.getConfig>);

    vi.mocked(jobsStore.getJobs).mockReturnValue([]);
    matchesHttpqlFilterMock.mockReturnValue(true);
  });

  describe("existing skip conditions", () => {
    it("does nothing when auto-crawl is disabled", async () => {
      vi.mocked(configStore.getConfig).mockReturnValue({
        enabled: false,
      } as ReturnType<typeof configStore.getConfig>);

      await AutoCrawlService.handleResponseForTest(makeRequest(), makeSdk());

      expect(startMock).not.toHaveBeenCalled();
    });

    it("skips static resources", async () => {
      const request = makeRequest({ getPath: () => "/assets/app.js" });

      await AutoCrawlService.handleResponseForTest(request, makeSdk());

      expect(startMock).not.toHaveBeenCalled();
    });

    it("skips requests outside the scope when crawlInScopeOnly is enabled", async () => {
      vi.mocked(configStore.getConfig).mockReturnValue({
        enabled: true,
        crawlInScopeOnly: true,
      } as ReturnType<typeof configStore.getConfig>);

      const sdk = makeSdk();
      vi.mocked(sdk.requests.inScope).mockReturnValue(false);

      await AutoCrawlService.handleResponseForTest(makeRequest(), sdk);

      expect(startMock).not.toHaveBeenCalled();
      expect(sdk.requests.inScope).toHaveBeenCalledOnce();
    });

    it("skips hosts rejected by the HTTPQL filter and remembers them", async () => {
      vi.mocked(configStore.getConfig).mockReturnValue({
        enabled: true,
        crawlInScopeOnly: false,
        httpqlFilter: 'req.host.eq:"other.com"',
      } as ReturnType<typeof configStore.getConfig>);
      matchesHttpqlFilterMock.mockReturnValue(false);

      const sdk = makeSdk();
      await AutoCrawlService.handleResponseForTest(makeRequest(), sdk);
      await AutoCrawlService.handleResponseForTest(makeRequest(), sdk);

      expect(startMock).not.toHaveBeenCalled();
      // The filter is evaluated only once because the host stays suppressed.
      expect(matchesHttpqlFilterMock).toHaveBeenCalledOnce();
      expect(AutoCrawlService.hasSeenHost("example.com")).toBe(true);
    });

    it("does not start a crawl when a job for the host already exists", async () => {
      vi.mocked(jobsStore.getJobs).mockReturnValue([
        makeJob("example.com", "job-9"),
      ]);

      await AutoCrawlService.handleResponseForTest(makeRequest(), makeSdk());

      expect(startMock).not.toHaveBeenCalled();
      expect(AutoCrawlService.hasSeenHost("example.com")).toBe(true);
    });

    it("starts a crawl for each distinct host", async () => {
      startMock
        .mockResolvedValueOnce(okResult("a.example.com"))
        .mockResolvedValueOnce(okResult("b.example.com"));

      const sdk = makeSdk();
      await AutoCrawlService.handleResponseForTest(
        makeRequest({ getHost: () => "a.example.com" }),
        sdk,
      );
      await AutoCrawlService.handleResponseForTest(
        makeRequest({ getHost: () => "b.example.com" }),
        sdk,
      );

      expect(startMock).toHaveBeenCalledTimes(2);
    });
  });

  describe("concurrent deduplication", () => {
    it("calls CrawlerService.start only once for concurrent interceptions", async () => {
      const gate = deferred<ReturnType<typeof okResult>>();
      startMock.mockReturnValue(gate.promise);

      const sdk = makeSdk();
      const request = makeRequest();

      // Fire several interceptions without awaiting, like concurrent events.
      const attempts = [
        AutoCrawlService.handleResponseForTest(request, sdk),
        AutoCrawlService.handleResponseForTest(request, sdk),
        AutoCrawlService.handleResponseForTest(request, sdk),
        AutoCrawlService.handleResponseForTest(request, sdk),
        AutoCrawlService.handleResponseForTest(request, sdk),
      ];

      // While the start call is pending, the host must already count as seen.
      expect(AutoCrawlService.hasSeenHost("example.com")).toBe(true);

      gate.resolve(OK_RESULT);
      await Promise.all(attempts);

      expect(startMock).toHaveBeenCalledTimes(1);
      expect(startMock).toHaveBeenCalledWith(
        "https://example.com",
        expect.any(Object),
      );
    });

    it("makes concurrent callers wait on the same in-flight result", async () => {
      const gate = deferred<ReturnType<typeof okResult>>();
      startMock.mockReturnValue(gate.promise);

      const sdk = makeSdk();

      const attempts = [
        AutoCrawlService.startCrawlForTest(
          "example.com",
          "https://example.com",
          sdk,
        ),
        AutoCrawlService.startCrawlForTest(
          "example.com",
          "https://example.com",
          sdk,
        ),
        AutoCrawlService.startCrawlForTest(
          "example.com",
          "https://example.com",
          sdk,
        ),
      ];

      expect(startMock).toHaveBeenCalledTimes(1);

      gate.resolve(OK_RESULT);
      const results = await Promise.all(attempts);

      expect(results).toHaveLength(3);
      for (const result of results) {
        expect(result).toBe(OK_RESULT);
      }
    });

    it("releases the host once the in-flight crawl settles", async () => {
      startMock.mockResolvedValue(okResult());

      const sdk = makeSdk();
      await AutoCrawlService.startCrawlForTest(
        "example.com",
        "https://example.com",
        sdk,
      );

      // No pending crawl anymore; only the persisted job keeps the host seen.
      expect(AutoCrawlService.hasSeenHost("example.com")).toBe(false);
    });
  });

  describe("retry after failure or task removal", () => {
    it("allows a new crawl after start returns an Error result", async () => {
      startMock
        .mockResolvedValueOnce({
          kind: "Error",
          error: "Invalid URL provided.",
        })
        .mockResolvedValueOnce(okResult());

      const sdk = makeSdk();
      await AutoCrawlService.handleResponseForTest(makeRequest(), sdk);

      expect(startMock).toHaveBeenCalledTimes(1);
      expect(AutoCrawlService.hasSeenHost("example.com")).toBe(false);

      await AutoCrawlService.handleResponseForTest(makeRequest(), sdk);
      expect(startMock).toHaveBeenCalledTimes(2);
    });

    it("allows a new crawl after start rejects", async () => {
      startMock
        .mockRejectedValueOnce(new Error("boom"))
        .mockResolvedValueOnce(okResult());

      const sdk = makeSdk();
      await AutoCrawlService.handleResponseForTest(makeRequest(), sdk);

      expect(AutoCrawlService.hasSeenHost("example.com")).toBe(false);

      await AutoCrawlService.handleResponseForTest(makeRequest(), sdk);
      expect(startMock).toHaveBeenCalledTimes(2);
    });

    it("re-triggers a crawl after the existing job is deleted", async () => {
      startMock
        .mockImplementationOnce(() => Promise.resolve(okResult()))
        .mockImplementationOnce(() => Promise.resolve(okResult()));

      const sdk = makeSdk();

      // First interception creates a job for the host.
      vi.mocked(jobsStore.getJobs).mockReturnValueOnce([]);
      await AutoCrawlService.handleResponseForTest(makeRequest(), sdk);
      expect(startMock).toHaveBeenCalledTimes(1);

      // While the job exists, interceptions are skipped.
      vi.mocked(jobsStore.getJobs).mockReturnValue([
        makeJob("example.com", "job-1"),
      ]);
      await AutoCrawlService.handleResponseForTest(makeRequest(), sdk);
      expect(startMock).toHaveBeenCalledTimes(1);

      // Job deleted: the host becomes triggerable again.
      vi.mocked(jobsStore.getJobs).mockReturnValue([]);
      await AutoCrawlService.handleResponseForTest(makeRequest(), sdk);
      expect(startMock).toHaveBeenCalledTimes(2);
    });

    it("re-triggers a crawl after all jobs are cleared", async () => {
      startMock.mockResolvedValue(okResult());

      const sdk = makeSdk();

      // First interception creates a job for the host.
      vi.mocked(jobsStore.getJobs).mockReturnValueOnce([]);
      await AutoCrawlService.handleResponseForTest(makeRequest(), sdk);
      expect(startMock).toHaveBeenCalledTimes(1);

      // With the job retained, the next interception must be skipped.
      vi.mocked(jobsStore.getJobs).mockReturnValueOnce([
        makeJob("example.com", "job-1"),
      ]);
      await AutoCrawlService.handleResponseForTest(makeRequest(), sdk);
      expect(startMock).toHaveBeenCalledTimes(1);

      // clearAllJobs() empties the store: the host is triggerable again.
      vi.mocked(jobsStore.getJobs).mockReturnValueOnce([]);
      await AutoCrawlService.handleResponseForTest(makeRequest(), sdk);
      expect(startMock).toHaveBeenCalledTimes(2);
    });

    it("does not re-trigger while another host's job is present", async () => {
      startMock.mockResolvedValue(okResult("example.com"));

      const sdk = makeSdk();
      vi.mocked(jobsStore.getJobs).mockReturnValue([
        makeJob("other.example.org", "job-2"),
      ]);

      await AutoCrawlService.handleResponseForTest(makeRequest(), sdk);

      expect(startMock).toHaveBeenCalledTimes(1);
    });
  });

  describe("seen-state helpers", () => {
    it("addSeenHost suppresses automatic crawls for the host", async () => {
      AutoCrawlService.addSeenHost("example.com");

      await AutoCrawlService.handleResponseForTest(makeRequest(), makeSdk());

      expect(startMock).not.toHaveBeenCalled();
      expect(AutoCrawlService.hasSeenHost("example.com")).toBe(true);
    });

    it("clearSeenHosts makes suppressed hosts evaluable again", async () => {
      vi.mocked(configStore.getConfig).mockReturnValue({
        enabled: true,
        crawlInScopeOnly: false,
        httpqlFilter: 'req.host.eq:"nope"',
      } as ReturnType<typeof configStore.getConfig>);
      matchesHttpqlFilterMock.mockReturnValue(false);

      const sdk = makeSdk();
      await AutoCrawlService.handleResponseForTest(makeRequest(), sdk);
      expect(AutoCrawlService.hasSeenHost("example.com")).toBe(true);

      AutoCrawlService.clearSeenHosts();
      expect(AutoCrawlService.hasSeenHost("example.com")).toBe(false);

      // A matching filter now lets the host through.
      matchesHttpqlFilterMock.mockReset().mockReturnValue(true);
      await AutoCrawlService.handleResponseForTest(makeRequest(), sdk);
      expect(startMock).toHaveBeenCalledTimes(1);
    });

    it("getSeenHosts combines pending, suppressed and job hosts", async () => {
      const gate = deferred<ReturnType<typeof okResult>>();
      startMock.mockReturnValue(gate.promise);
      vi.mocked(jobsStore.getJobs).mockReturnValue([
        makeJob("stored.example.org", "job-3"),
      ]);
      AutoCrawlService.addSeenHost("suppressed.example.net");

      const pending = AutoCrawlService.handleResponseForTest(
        makeRequest({ getHost: () => "pending.example.com" }),
        makeSdk(),
      );

      expect(AutoCrawlService.getSeenHosts().sort()).toEqual(
        [
          "pending.example.com",
          "stored.example.org",
          "suppressed.example.net",
        ].sort(),
      );

      gate.resolve(okResult("pending.example.com"));
      await pending;
    });
  });
});
