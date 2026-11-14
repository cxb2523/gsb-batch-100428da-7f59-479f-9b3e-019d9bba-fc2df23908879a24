import { describe, expect, it } from "vitest";
import type { SourceCandidate } from "./contracts";
import {
  CollectionOutcomeTracker,
  dedupeKey,
  normalizeTitle,
  partitionByDedupe,
  summarizeCollectionOutcomes,
} from "./outcomes";

function candidate(
  overrides: Partial<SourceCandidate> & Pick<SourceCandidate, "url" | "title">
): SourceCandidate {
  return {
    sourceName: new URL(overrides.url).hostname,
    text: "x".repeat(120),
    ...overrides,
  };
}

describe("collection outcome tracker", () => {
  it("keeps only the final outcome when the same candidate is attempted more than once", () => {
    const tracker = new CollectionOutcomeTracker();
    const item = {
      url: "https://example.com/a",
      sourceName: "example.com",
      title: "First attempt",
    };

    tracker.record(item, "request_failed", "connection timed out");
    tracker.record(item, "accepted", "retry succeeded");

    expect(tracker.outcomes()).toHaveLength(1);
    expect(tracker.outcomes()[0]).toMatchObject({
      url: item.url,
      status: "accepted",
    });
  });

  it("counts each candidate URL exactly once even after several state transitions", () => {
    const tracker = new CollectionOutcomeTracker();
    const item = {
      url: "https://example.com/retried",
      sourceName: "example.com",
      title: "Retried item",
    };

    tracker.record(item, "request_failed");
    tracker.record(item, "content_too_short");
    tracker.record(item, "accepted");

    const summary = summarizeCollectionOutcomes(tracker.outcomes());
    expect(summary.accepted).toBe(1);
    expect(summary.request_failed).toBe(0);
    expect(summary.content_too_short).toBe(0);
    expect(
      Object.values(summary).reduce((total, count) => total + count, 0)
    ).toBe(1);
  });

  it("replays initial outcomes so a shared ledger stays deduplicated by URL", () => {
    const tracker = new CollectionOutcomeTracker([
      {
        url: "https://example.com/seed",
        sourceName: "example.com",
        title: "Seed",
        status: "request_failed",
      },
    ]);
    expect(tracker.get("https://example.com/seed")?.status).toBe(
      "request_failed"
    );
  });
});

describe("candidate de-duplication outcomes", () => {
  it("uses normalized titles so only punctuation and casing differ", () => {
    expect(normalizeTitle("Breaking: Market-Shift!")).toBe(
      normalizeTitle("breaking market shift")
    );
    expect(
      dedupeKey(candidate({ url: "https://a.test/1", title: "Same Headline?" }))
    ).toBe(
      dedupeKey(candidate({ url: "https://b.test/2", title: "same  headline" }))
    );
  });

  it("marks later normalized-title matches as duplicates instead of successful items", () => {
    const tracker = new CollectionOutcomeTracker();
    const first = candidate({
      url: "https://a.test/1",
      title: "Enterprise agents launch platform",
      sourceName: "a.test",
    });
    const second = candidate({
      url: "https://b.test/2",
      title: "Enterprise Agents, Launch Platform!",
      sourceName: "b.test",
    });
    const third = candidate({
      url: "https://c.test/3",
      title: "A completely different headline",
      sourceName: "c.test",
    });

    const kept = partitionByDedupe([first, second, third], tracker);

    expect(kept.map(item => item.url)).toEqual([first.url, third.url]);
    expect(tracker.get(second.url)).toMatchObject({
      status: "duplicate_title",
      title: second.title,
      sourceName: "b.test",
    });
    expect(tracker.get(first.url)).toBeUndefined();
    expect(tracker.get(third.url)).toBeUndefined();
  });

  it("falls back to a URL fingerprint when a title normalizes to empty", () => {
    const tracker = new CollectionOutcomeTracker();
    const first = candidate({ url: "https://a.test/1", title: "!!!" });
    const second = candidate({ url: "https://a.test/1", title: "???" });
    const third = candidate({ url: "https://other.test/x", title: "..." });

    const kept = partitionByDedupe([first, second, third], tracker);

    expect(kept.map(item => item.url)).toEqual([
      "https://a.test/1",
      "https://other.test/x",
    ]);
    expect(tracker.get(second.url)?.status).toBe("duplicate_title");
  });
});
