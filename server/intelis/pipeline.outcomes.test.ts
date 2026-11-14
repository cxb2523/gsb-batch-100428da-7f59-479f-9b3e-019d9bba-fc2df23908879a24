import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ResearchTask } from "../../drizzle/schema";

const mocks = vi.hoisted(() => ({
  createPipelineRun: vi.fn(),
  updatePipelineRun: vi.fn(),
  saveFindings: vi.fn(),
  saveSnapshots: vi.fn(),
  recordEntityChangeEvents: vi.fn(),
  markFollowedEntitiesObserved: vi.fn(),
  priorFingerprints: vi.fn(),
  historicalEntityNames: vi.fn(),
  saveTrend: vi.fn(),
  saveReport: vi.fn(),
  recordDelivery: vi.fn(),
}));

vi.mock("./repository", () => ({
  createPipelineRun: mocks.createPipelineRun,
  updatePipelineRun: mocks.updatePipelineRun,
  saveFindings: mocks.saveFindings,
  saveSnapshots: mocks.saveSnapshots,
  recordEntityChangeEvents: mocks.recordEntityChangeEvents,
  markFollowedEntitiesObserved: mocks.markFollowedEntitiesObserved,
  priorFingerprints: mocks.priorFingerprints,
  historicalEntityNames: mocks.historicalEntityNames,
  saveTrend: mocks.saveTrend,
  saveReport: mocks.saveReport,
  recordDelivery: mocks.recordDelivery,
}));

vi.mock("./groq", () => ({
  analyzeCandidate: vi.fn().mockResolvedValue({
    summary: "Synthesized summary",
    category: "Market",
    entities: [],
    qualityScore: 80,
    relevanceScore: 80,
    credibilityScore: 80,
    noveltyScore: 80,
    verificationStatus: "verified",
  }),
  synthesizeReport: vi
    .fn()
    .mockResolvedValue({ title: "Report", content: "report body" }),
  synthesizeDigest: vi
    .fn()
    .mockResolvedValue({ title: "Digest", content: "digest body" }),
  synthesizeTrendAnalysis: vi
    .fn()
    .mockResolvedValue({ trends: [], anomalies: [] }),
}));

vi.mock("../_core/notification", () => ({
  notifyOwner: vi.fn().mockResolvedValue(undefined),
}));

import { runResearchPipeline } from "./pipeline";

const longText = "material development ".repeat(10);

const task = {
  id: 7,
  userId: 3,
  collectionId: null,
  name: "Competitive watch",
  naturalLanguageRequest: "Monitor enterprise AI agent deployments",
  sources: ["web"],
  keywords: ["agents"],
  topics: [],
  sourceFilters: { include: [], exclude: [] },
  cronExpression: "0 9 * * * *",
  executionProfile: "scheduled",
  status: "active",
  emailEnabled: false,
  deliveryEmail: null,
} as ResearchTask;

describe("runResearchPipeline collection outcome persistence", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.createPipelineRun.mockResolvedValue(101);
    mocks.saveFindings.mockImplementation(
      async (
        _runId: number,
        _taskId: number,
        _userId: number,
        findings: unknown[]
      ) => findings
    );
    mocks.recordEntityChangeEvents.mockResolvedValue({ anomalies: [] });
    mocks.priorFingerprints.mockResolvedValue([]);
    mocks.historicalEntityNames.mockResolvedValue([]);
    mocks.saveReport.mockResolvedValue(1);
  });

  it("persists a per-candidate ledger covering all five terminal statuses with internally consistent counts", async () => {
    const feedUrl = "https://api.gdeltproject.org/api/v2/doc/doc";
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);

      if (url.startsWith(feedUrl)) {
        return new Response(
          JSON.stringify({
            articles: [
              {
                title: "Accepted story",
                url: "https://ok.example.com/story",
                domain: "ok.example.com",
              },
              {
                title: "Thin story",
                url: "https://thin.example.com/story",
                domain: "thin.example.com",
              },
              {
                title: "Blocked story",
                url: "http://10.0.0.8/internal",
                domain: "10.0.0.8",
              },
              {
                title: "Timeout story",
                url: "https://slow.example.com/story",
                domain: "slow.example.com",
              },
              {
                title: "First duplicate",
                url: "https://dup-a.example.com/story",
                domain: "dup-a.example.com",
              },
              {
                title: "First duplicate!",
                url: "https://dup-b.example.com/story",
                domain: "dup-b.example.com",
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      if (
        url === "https://ok.example.com/story" ||
        url.startsWith("https://dup-")
      ) {
        return new Response(`<html><body>${longText}</body></html>`, {
          status: 200,
        });
      }
      if (url === "https://thin.example.com/story") {
        return new Response("<html><body>short</body></html>", { status: 200 });
      }
      if (url === "https://slow.example.com/story") {
        return Promise.reject(new TypeError("request timed out"));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await runResearchPipeline(task, "manual");
    expect(result).toMatchObject({
      runId: 101,
      sourceCount: 3,
      findingCount: 2,
    });

    const completionUpdate = mocks.updatePipelineRun.mock.calls
      .map(call => call[1] as Record<string, unknown>)
      .find(
        values => values.status === "completed" || values.status === "partial"
      );
    expect(completionUpdate).toBeDefined();

    const outcomes = completionUpdate!.collectionOutcomes as Array<{
      url: string;
      status: string;
      title: string;
      sourceName: string;
    }>;
    const statusOf = (url: string) =>
      outcomes.find(outcome => outcome.url === url)?.status;

    expect(statusOf("https://ok.example.com/story")).toBe("accepted");
    expect(statusOf("http://10.0.0.8/internal")).toBe("rejected_by_safety");
    expect(statusOf("https://slow.example.com/story")).toBe("request_failed");
    expect(statusOf("https://thin.example.com/story")).toBe(
      "content_too_short"
    );
    // Duplicates follow the normalized-title rule and are never counted as accepted.
    expect(statusOf("https://dup-b.example.com/story")).toBe("duplicate_title");
    expect(statusOf("https://dup-a.example.com/story")).toBe("accepted");

    // One row per candidate URL — no double counting after any state transition.
    expect(new Set(outcomes.map(outcome => outcome.url)).size).toBe(
      outcomes.length
    );

    const counts: Record<string, number> = {};
    for (const outcome of outcomes)
      counts[outcome.status] = (counts[outcome.status] ?? 0) + 1;
    expect(Object.values(counts).reduce((sum, count) => sum + count, 0)).toBe(
      outcomes.length
    );
    expect(counts).toMatchObject({
      accepted: 2,
      content_too_short: 1,
      duplicate_title: 1,
      rejected_by_safety: 1,
      request_failed: 1,
    });
  });
});
