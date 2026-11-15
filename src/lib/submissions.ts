export type CancelReason = "superseded" | "mode-change";

export type SubmissionOutcome =
  | { kind: "pending" }
  | { kind: "success"; httpStatus: number; finishedAt: number }
  | {
      kind: "server-rejected";
      httpStatus: number;
      statusText: string;
      detail?: string;
      finishedAt: number;
    }
  | { kind: "network-error"; message: string; finishedAt: number }
  | {
      kind: "malformed-response";
      httpStatus: number;
      message: string;
      finishedAt: number;
    }
  | {
      kind: "cancelled";
      reason: CancelReason;
      detail: string;
      finishedAt: number;
    };

export type Submission = {
  id: number;
  mode: string;
  url: string;
  startedAt: number;
  outcome: SubmissionOutcome;
};

export type RenderKind = "json" | "text";

export type StoredContent = {
  submissionId: number;
  mode: string;
  renderKind: RenderKind;
  text: string;
  receivedAt: number;
};

const MODE_LABELS: Record<string, string> = {
  llmExtraction: "LLM Extraction",
  crawl: "Crawl",
  static: "Static",
  dynamic: "Dynamic",
};

export function modeLabel(mode: string | undefined): string {
  if (!mode) {
    return "No mode";
  }
  return MODE_LABELS[mode] ?? mode;
}

export function summarizeOutcome(outcome: SubmissionOutcome): string {
  switch (outcome.kind) {
    case "pending":
      return "In progress…";
    case "success":
      return `Success — HTTP ${outcome.httpStatus}`;
    case "server-rejected":
      return `Rejected — HTTP ${outcome.httpStatus}${
        outcome.statusText ? ` ${outcome.statusText}` : ""
      }`;
    case "network-error":
      return `Network error — ${outcome.message}`;
    case "malformed-response":
      return `Invalid response — HTTP ${outcome.httpStatus}`;
    case "cancelled":
      return outcome.reason === "superseded"
        ? "Cancelled — superseded by a newer submission"
        : "Cancelled — mode switched";
  }
}

export function outcomeDetail(outcome: SubmissionOutcome): string | undefined {
  switch (outcome.kind) {
    case "pending":
      return "Waiting for the workflow to respond.";
    case "success":
      return "The workflow accepted the request and returned a response.";
    case "server-rejected":
      return outcome.detail;
    case "network-error":
      return "The request did not receive a response from the workflow.";
    case "malformed-response":
      return outcome.message;
    case "cancelled":
      return outcome.detail;
  }
}

export function outcomeFinishedAt(outcome: SubmissionOutcome): number | undefined {
  return "finishedAt" in outcome ? outcome.finishedAt : undefined;
}

export function submissionDuration(submission: Submission): number | undefined {
  const finishedAt = outcomeFinishedAt(submission.outcome);
  return finishedAt === undefined ? undefined : finishedAt - submission.startedAt;
}

export function formatClock(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString([], { hour12: false });
}

export function formatDuration(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function describeError(error: unknown): string {
  if (error instanceof Error) {
    return error.message || error.name;
  }
  return String(error);
}

export function truncate(value: string, maxLength = 500): string {
  if (value.length <= maxLength) {
    return value;
  }
  return `${value.slice(0, maxLength)}…`;
}

/**
 * Reads a successful workflow response according to the selected scrape mode.
 *
 * The display/parsing contract of the playground must stay stable:
 * - crawl and llmExtraction responses are shown as JSON,
 * - dynamic responses are shown as plain text,
 * - static responses read the `content` field from the JSON body.
 */
export async function readScrapeResponse(
  mode: string,
  response: Response
): Promise<{ text: string; renderKind: RenderKind }> {
  if (mode === "dynamic") {
    return { text: await response.text(), renderKind: "text" };
  }

  const data: unknown = await response.json();

  if (mode === "crawl" || mode === "llmExtraction") {
    return {
      text: JSON.stringify(data, null, 2) ?? String(data),
      renderKind: "json",
    };
  }

  const content =
    typeof data === "object" && data !== null
      ? (data as { content?: unknown }).content
      : undefined;

  if (typeof content === "string") {
    return { text: content, renderKind: "text" };
  }
  if (content === undefined || content === null) {
    return { text: "", renderKind: "text" };
  }
  return { text: JSON.stringify(content, null, 2) ?? String(content), renderKind: "text" };
}
