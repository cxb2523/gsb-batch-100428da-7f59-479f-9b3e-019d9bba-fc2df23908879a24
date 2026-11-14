import type { CrawlJob, Result } from "shared";

import { configStore } from "../stores/configStore";
import { jobsStore } from "../stores/jobsStore";
import type { BackendSDK, InterceptedRequest } from "../types";

import { CrawlerService } from "./crawler";
import { matchesHttpqlFilter } from "./crawler/httpqlFilter";

type StartResult = Result<{ job: CrawlJob; jobId: string }>;

interface PendingCrawl {
  /**
   * Promise of the in-flight CrawlerService.start call. Concurrent
   * interceptions for the same host wait on this same promise instead of
   * starting a second crawl.
   */
  promise: Promise<StartResult>;
}

class AutoCrawlServiceClass {
  /**
   * Hosts whose automatic crawl is currently being started. A host stays in
   * this map for the whole duration of the (asynchronous) start call so that
   * concurrent interceptions are deduplicated onto a single task.
   */
  private pendingCrawls: Map<string, PendingCrawl> = new Map();

  /**
   * Hosts that were evaluated but must not start an automatic crawl, either
   * because they did not match the configured HTTPQL filter or were marked as
   * seen manually. The persisted jobs store is the other source of "seen"
   * state: as soon as a job for a host exists, that host is considered seen.
   */
  private suppressedHosts: Set<string> = new Set();

  init(sdk: BackendSDK): void {
    sdk.events.onInterceptResponse((_, request) => {
      void this.handleResponse(request, sdk);
    });
  }

  private async handleResponse(
    request: InterceptedRequest,
    sdk: BackendSDK,
  ): Promise<void> {
    const config = configStore.getConfig();

    if (!config.enabled) {
      return;
    }

    const host = request.getHost();

    if (this.isStaticResource(request.getPath())) {
      return;
    }

    if (config.crawlInScopeOnly) {
      const inScope = sdk.requests.inScope(request);
      if (!inScope) {
        return;
      }
    }

    // Already seen: an automatic crawl is in flight, the host was suppressed
    // (e.g. HTTPQL mismatch), or a job for it already exists in the store.
    if (this.isHostKnown(host)) {
      return;
    }

    const url = this.buildUrl(request);

    const httpqlFilter = config.httpqlFilter?.trim();
    if (httpqlFilter !== undefined && httpqlFilter !== "") {
      if (!matchesHttpqlFilter(url, httpqlFilter)) {
        this.suppressedHosts.add(host);
        return;
      }
    }

    await this.startCrawlForHost(host, url, sdk);
  }

  /**
   * Starts an automatic crawl for a host, deduplicating concurrent callers.
   * The first caller creates and tracks the start promise; every subsequent
   * interception of the same host awaits the same promise instead of calling
   * CrawlerService.start again.
   */
  private startCrawlForHost(
    host: string,
    url: string,
    sdk: BackendSDK,
  ): Promise<StartResult> {
    const pending = this.pendingCrawls.get(host);
    if (pending !== undefined) {
      return pending.promise;
    }

    const entry: PendingCrawl = {
      promise: this.startCrawl(url, sdk).catch(
        (error: unknown): StartResult => {
          // Normalize a synchronous/rejected start into an Error result so the
          // host becomes retryable instead of staying silently tracked.
          return {
            kind: "Error",
            error: error instanceof Error ? error.message : String(error),
          };
        },
      ),
    };
    this.pendingCrawls.set(host, entry);

    return entry.promise.finally(() => {
      // Only clear the entry if it is still the task we created. A newer task
      // for the host must never be evicted by a stale completion.
      if (this.pendingCrawls.get(host) === entry) {
        this.pendingCrawls.delete(host);
      }
    });
  }

  /**
   * "Seen" state is derived from the real task state rather than from a
   * standalone set: a host is known while a crawl is in flight, while it is
   * explicitly suppressed, or while a job for it exists in the jobs store.
   * When the job is deleted or all jobs are cleared, the host is unknown
   * again and the next intercepted request can re-trigger a crawl.
   */
  private isHostKnown(host: string): boolean {
    if (this.pendingCrawls.has(host) || this.suppressedHosts.has(host)) {
      return true;
    }

    return jobsStore.getJobs().some((job) => job.host === host);
  }

  private buildUrl(request: InterceptedRequest): string {
    const host = request.getHost();
    const port = request.getPort();
    const isTls = request.getTls();

    const protocol = isTls ? "https" : "http";
    const defaultPort = isTls ? 443 : 80;

    if (port === defaultPort) {
      return `${protocol}://${host}`;
    }

    return `${protocol}://${host}:${port}`;
  }

  private startCrawl(url: string, sdk: BackendSDK): Promise<StartResult> {
    return CrawlerService.start(url, {
      onProgress: (stats, jobId) => {
        sdk.api.send("crawl:progress", {
          jobId,
          crawled: stats.crawledUrls,
          discovered: stats.discoveredUrls,
        });
      },
      onComplete: (stats, jobId) => {
        sdk.api.send("crawl:completed", {
          jobId,
          totalUrls: stats.crawledUrls,
        });
      },
    }).then((result) => {
      if (result.kind === "Ok") {
        const { job, jobId } = result.value;
        sdk.api.send("job:created", job);
        sdk.api.send("crawl:started", { jobId, host: job.host });
      }

      return result;
    });
  }

  private isStaticResource(path: string): boolean {
    const staticExtensions = [
      ".js",
      ".css",
      ".map",
      ".png",
      ".jpg",
      ".jpeg",
      ".gif",
      ".svg",
      ".ico",
      ".woff",
      ".woff2",
      ".ttf",
      ".eot",
      ".webp",
      ".mp4",
      ".webm",
      ".mp3",
      ".wav",
      ".pdf",
    ];

    return staticExtensions.some((ext) => path.toLowerCase().endsWith(ext));
  }

  /**
   * Clears the in-memory suppression memory so hosts filtered out earlier
   * (e.g. by an HTTPQL filter) can be evaluated again by the next request.
   * In-flight crawls are deliberately left untouched so their deduplication
   * guarantee is preserved, and persisted jobs are left untouched so hosts
   * that still have a job remain seen until that job is deleted or cleared.
   */
  clearSeenHosts(): void {
    this.suppressedHosts.clear();
  }

  getSeenHosts(): string[] {
    const hosts = new Set<string>(this.suppressedHosts);
    for (const host of this.pendingCrawls.keys()) {
      hosts.add(host);
    }
    for (const job of jobsStore.getJobs()) {
      hosts.add(job.host);
    }
    return Array.from(hosts);
  }

  hasSeenHost(host: string): boolean {
    return this.isHostKnown(host);
  }

  addSeenHost(host: string): void {
    this.suppressedHosts.add(host);
  }

  /**
   * Test-only entry points. They expose the internal scheduling entry points
   * without going through the Caido interception event registration.
   */
  handleResponseForTest(
    request: InterceptedRequest,
    sdk: BackendSDK,
  ): Promise<void> {
    return this.handleResponse(request, sdk);
  }

  startCrawlForTest(
    host: string,
    url: string,
    sdk: BackendSDK,
  ): Promise<StartResult> {
    return this.startCrawlForHost(host, url, sdk);
  }
}

export const AutoCrawlService = new AutoCrawlServiceClass();
