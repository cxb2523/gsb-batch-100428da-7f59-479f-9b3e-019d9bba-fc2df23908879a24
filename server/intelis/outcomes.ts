import crypto from "node:crypto";
import type {
  CollectionOutcome,
  CollectionOutcomeStatus,
  SourceCandidate,
} from "./contracts";

/**
 * Title normalization shared by the de-duplication stage and the collection
 * outcome ledger so that "duplicate" decisions always use the same key.
 */
export function normalizeTitle(value: string) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function urlFingerprint(url: string) {
  return crypto
    .createHash("sha256")
    .update(url.trim().toLowerCase())
    .digest("hex");
}

/**
 * Canonical de-duplication key: a normalized title, falling back to a URL
 * fingerprint when a candidate carries no usable title. This mirrors the
 * historical pipeline behavior exactly.
 */
export function dedupeKey(candidate: Pick<SourceCandidate, "title" | "url">) {
  return normalizeTitle(candidate.title) || urlFingerprint(candidate.url);
}

/**
 * Keeps one final {@link CollectionOutcome} per candidate URL. When the same
 * candidate is observed more than once (for example a fetch that initially
 * fails and later retries successfully), the latest record overwrites the
 * earlier one so each candidate is counted exactly once with its final state.
 */
export class CollectionOutcomeTracker {
  private readonly records = new Map<string, CollectionOutcome>();

  constructor(initial: CollectionOutcome[] = []) {
    for (const outcome of initial) this.records.set(outcome.url, outcome);
  }

  record(
    candidate: Pick<SourceCandidate, "url" | "sourceName" | "title">,
    status: CollectionOutcomeStatus,
    detail?: string
  ) {
    this.records.set(candidate.url, {
      url: candidate.url,
      sourceName: candidate.sourceName,
      title: candidate.title,
      status,
      ...(detail ? { detail } : {}),
    });
  }

  get(url: string) {
    return this.records.get(url);
  }

  outcomes(): CollectionOutcome[] {
    return Array.from(this.records.values());
  }
}

/**
 * Splits candidates into survivors and duplicates using the exact same
 * normalized-title rule as the original pipeline filter. Duplicates are
 * reported on the supplied tracker instead of silently disappearing.
 */
export function partitionByDedupe(
  candidates: SourceCandidate[],
  tracker: CollectionOutcomeTracker
) {
  const seen = new Set<string>();
  const deduplicated: SourceCandidate[] = [];
  for (const candidate of candidates) {
    const key = dedupeKey(candidate);
    if (seen.has(key)) {
      tracker.record(
        candidate,
        "duplicate_title",
        "Another candidate with the same normalized title was already kept."
      );
      continue;
    }
    seen.add(key);
    deduplicated.push(candidate);
  }
  return deduplicated;
}

export function summarizeCollectionOutcomes(outcomes: CollectionOutcome[]) {
  const summary: Record<CollectionOutcomeStatus, number> = {
    rejected_by_safety: 0,
    request_failed: 0,
    content_too_short: 0,
    duplicate_title: 0,
    accepted: 0,
  };
  for (const outcome of outcomes) summary[outcome.status] += 1;
  return summary;
}
