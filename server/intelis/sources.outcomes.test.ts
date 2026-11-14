import { afterEach, describe, expect, it, vi } from "vitest";
import type { CollectionOutcome, SourceCandidate } from "./contracts";
import {
  CollectionOutcomeTracker,
  partitionByDedupe,
  summarizeCollectionOutcomes,
} from "./outcomes";
import {
  crawlSourceCandidates,
  collectSourceCandidates,
  isSafePublicHttpUrl,
} from "./sources";

const longBody = (marker: string) =>
  `<html><body><p>${marker} ${"word ".repeat(40)}</p></body></html>`;

const rssFeed = (
  items: Array<{ title: string; link: string; description: string }>
) =>
  `<?xml version="1.0"?><rss version="2.0"><channel>${items
    .map(
      item =>
        `<item><title>${item.title}</title><link>${item.link}</link><description>${item.description}</description></item>`
    )
    .join("")}</channel></rss>`;

const TASK = {
  naturalLanguageRequest: "Monitor enterprise AI agents",
  sources: ["web", "rss"],
  keywords: ["agents"],
  topics: [],
  sourceFilters: {
    include: [
      "https://feeds.example.com/journal.xml",
      "https://direct.example.com/briefing",
      "http://127.0.0.1:3000/internal-feed",
    ],
    exclude: [],
  },
};

const gdeltUrl = "https://api.gdeltproject.org/api/v2/doc/doc";

function statusByUrl(outcomes: CollectionOutcome[], url: string) {
  return outcomes.find(outcome => outcome.url === url)?.status;
}

describe("collectSourceCandidates outcome ledger", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("records a verifiable final status for every candidate and keeps the status counts consistent", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);

      if (url.startsWith(gdeltUrl)) {
        return new Response(
          JSON.stringify({
            articles: [
              {
                title: "Successful discovery article",
                url: "https://news.example.com/full",
                domain: "news.example.com",
                seendate: "20260101T000000Z",
              },
              {
                title: "Thin discovery article",
                url: "https://thin.example.com/short",
                domain: "thin.example.com",
              },
              {
                title: "Private discovery article",
                url: "http://192.168.1.50/secret",
                domain: "192.168.1.50",
              },
              {
                title: "Timed out discovery article",
                url: "https://slow.example.com/late",
                domain: "slow.example.com",
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }

      if (url === "https://feeds.example.com/journal.xml") {
        return new Response(
          rssFeed([
            {
              title: "RSS original story",
              link: "https://rss.example.com/original",
              description: longBody("rss-original"),
            },
            {
              title: "RSS duplicate story",
              link: "https://rss.example.com/duplicate",
              description: longBody("rss-duplicate"),
            },
          ]),
          { status: 200, headers: { "Content-Type": "application/xml" } }
        );
      }

      if (url === "https://direct.example.com/briefing") {
        return new Response(
          `<html><head><title>Direct briefing duplicate</title></head><body>${longBody("direct")}</body></html>`,
          { status: 200 }
        );
      }

      if (
        url === "https://rss.example.com/original" ||
        url === "https://rss.example.com/duplicate"
      ) {
        return new Response(longBody("rss-crawl"), { status: 200 });
      }

      if (url === "https://news.example.com/full") {
        return new Response(longBody("full-article"), { status: 200 });
      }

      if (url === "https://thin.example.com/short") {
        return new Response("<html><body>tiny</body></html>", { status: 200 });
      }

      if (url === "https://slow.example.com/late") {
        return Promise.reject(new TypeError("network timeout"));
      }

      throw new Error(`Unexpected fetch in test: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const tracker = new CollectionOutcomeTracker();
    const { candidates } = await collectSourceCandidates(TASK, 20, tracker);
    const outcomes = tracker.outcomes();

    // The usable candidate list keeps the original contract: only viable items.
    // Include URLs feed both the RSS and direct-page collectors (pre-existing behavior).
    const candidateUrls = candidates.map(item => item.url).sort();
    expect(candidateUrls).toEqual(
      [
        "https://news.example.com/full",
        "https://rss.example.com/original",
        "https://rss.example.com/duplicate",
        "https://direct.example.com/briefing",
        "https://feeds.example.com/journal.xml",
      ].sort()
    );

    // 1. Rejected by the existing public HTTP(S) safety rule (no rule relaxation)
    expect(isSafePublicHttpUrl("http://127.0.0.1:3000/internal-feed")).toBe(
      false
    );
    expect(isSafePublicHttpUrl("http://192.168.1.50/secret")).toBe(false);
    // Safety rejections never result in a network request.
    const fetchedUrls = fetchMock.mock.calls.map(call => String(call[0]));
    expect(fetchedUrls.some(url => url.includes("127.0.0.1"))).toBe(false);
    expect(fetchedUrls.some(url => url.includes("192.168.1.50"))).toBe(false);

    // 2. Request failure (including timeout)
    expect(
      statusByUrl(tracker.outcomes(), "https://slow.example.com/late")
    ).toBe("request_failed");

    // 3. Body shorter than 80 characters
    expect(
      statusByUrl(tracker.outcomes(), "https://thin.example.com/short")
    ).toBe("content_too_short");

    // 4. Title duplicate resolved with the pipeline's normalized-title de-dupe rule
    const crawled = await crawlSourceCandidates(candidates, tracker);
    const duplicateTitle = crawled.find(
      item => item.url === "https://rss.example.com/duplicate"
    );
    const sameTitle = { ...duplicateTitle!, title: "Rss Original Story?" };
    const survivors = partitionByDedupe(
      [
        crawled.find(item => item.url === "https://rss.example.com/original")!,
        sameTitle,
      ],
      tracker
    );
    expect(survivors).toHaveLength(1);

    // 5. Accepted into later analysis (the pipeline marks every surviving candidate once it enters analysis)
    for (const survivor of survivors) {
      tracker.record(
        survivor,
        "accepted",
        "Candidate entered the intelligence analysis stage."
      );
    }
    const survivingCandidate = crawled.find(
      item => item.url === "https://news.example.com/full"
    );
    if (survivingCandidate)
      tracker.record(
        survivingCandidate,
        "accepted",
        "Candidate entered the intelligence analysis stage."
      );

    // Re-read the ledger after the crawl / de-dupe / acceptance state transitions.
    const finalOutcomes = tracker.outcomes();

    // The five required outcome categories are all present with verifiable details.
    expect(
      statusByUrl(finalOutcomes, "http://127.0.0.1:3000/internal-feed")
    ).toBe("rejected_by_safety");
    expect(statusByUrl(finalOutcomes, "http://192.168.1.50/secret")).toBe(
      "rejected_by_safety"
    );
    expect(statusByUrl(finalOutcomes, "https://slow.example.com/late")).toBe(
      "request_failed"
    );
    expect(statusByUrl(finalOutcomes, "https://thin.example.com/short")).toBe(
      "content_too_short"
    );
    expect(
      statusByUrl(finalOutcomes, "https://rss.example.com/duplicate")
    ).toBe("duplicate_title");
    expect(statusByUrl(finalOutcomes, "https://news.example.com/full")).toBe(
      "accepted"
    );
    const accepted = finalOutcomes.find(
      outcome => outcome.url === "https://news.example.com/full"
    );
    expect(accepted).toMatchObject({
      title: "Successful discovery article",
      sourceName: "news.example.com",
    });

    // Every outcome carries the auditable fields requested by the run details view.
    for (const outcome of finalOutcomes) {
      expect(outcome.url).toEqual(expect.any(String));
      expect(outcome.sourceName).toEqual(expect.any(String));
      expect(outcome.title).toEqual(expect.any(String));
      expect([
        "rejected_by_safety",
        "request_failed",
        "content_too_short",
        "duplicate_title",
        "accepted",
      ]).toContain(outcome.status);
    }

    // One record per candidate URL: no double counting after state transitions.
    expect(new Set(finalOutcomes.map(outcome => outcome.url)).size).toBe(
      finalOutcomes.length
    );

    // Total equals the sum of the per-status buckets, and every required bucket is represented.
    const summary = summarizeCollectionOutcomes(finalOutcomes);
    const total = Object.values(summary).reduce((sum, count) => sum + count, 0);
    expect(total).toBe(finalOutcomes.length);
    expect(summary.rejected_by_safety).toBe(2);
    expect(summary.request_failed).toBe(1);
    expect(summary.content_too_short).toBe(1);
    expect(summary.duplicate_title).toBe(1);
    expect(summary.accepted).toBeGreaterThanOrEqual(1);
  });

  it("replaces an earlier failure with the final outcome when the same URL is retried successfully", async () => {
    const tracker = new CollectionOutcomeTracker();
    const candidate: SourceCandidate = {
      title: "Retried candidate",
      url: "https://retry.example.com/story",
      sourceName: "retry.example.com",
      text: longBody("retry"),
    };

    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockRejectedValueOnce(new TypeError("connection reset"))
        .mockResolvedValueOnce(new Response(longBody("retry"), { status: 200 }))
    );

    const firstAttempt = await crawlSourceCandidates([candidate], tracker);
    expect(firstAttempt).toHaveLength(0);
    expect(tracker.get(candidate.url)?.status).toBe("request_failed");

    const secondAttempt = await crawlSourceCandidates([candidate], tracker);
    expect(secondAttempt.map(item => item.url)).toEqual([candidate.url]);

    // The pipeline records "accepted" once the surviving candidate enters analysis.
    tracker.record(
      candidate,
      "accepted",
      "Candidate entered the intelligence analysis stage."
    );

    const finalOutcomes = tracker.outcomes();
    expect(finalOutcomes).toHaveLength(1);
    expect(finalOutcomes[0]).toMatchObject({
      url: candidate.url,
      status: "accepted",
    });
  });
});
