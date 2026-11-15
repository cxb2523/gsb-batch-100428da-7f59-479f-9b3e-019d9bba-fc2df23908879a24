import { Check, Clipboard } from "lucide-react";
import JSONPretty from "react-json-pretty";
import { Button } from "@/components/ui/button";
import {
  formatClock,
  formatDuration,
  modeLabel,
  outcomeDetail,
  submissionDuration,
  summarizeOutcome,
} from "@/lib/submissions";
import type {
  StoredContent,
  Submission,
  SubmissionOutcome,
} from "@/lib/submissions";

function isValidJSON(value: string) {
  try {
    JSON.parse(value);
    return true;
  } catch {
    return false;
  }
}

function outcomeTone(outcome: SubmissionOutcome) {
  switch (outcome.kind) {
    case "success":
      return "border-emerald-500/50 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400";
    case "server-rejected":
    case "malformed-response":
    case "network-error":
      return "border-destructive/50 bg-destructive/10 text-destructive";
    case "cancelled":
      return "border-amber-500/50 bg-amber-500/10 text-amber-600 dark:text-amber-400";
    case "pending":
    default:
      return "border-border bg-background text-muted-foreground";
  }
}

function httpStatusOf(outcome: SubmissionOutcome): number | undefined {
  return "httpStatus" in outcome ? outcome.httpStatus : undefined;
}

function OutcomeBadge({ outcome }: { outcome: SubmissionOutcome }) {
  return (
    <span
      className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${outcomeTone(
        outcome
      )}`}
    >
      {summarizeOutcome(outcome)}
    </span>
  );
}

function SubmissionSummary({ submission }: { submission: Submission }) {
  const duration = submissionDuration(submission);
  const detail = outcomeDetail(submission.outcome);

  return (
    <div
      data-testid="submission-outcome"
      data-submission-id={submission.id}
      data-outcome={submission.outcome.kind}
      data-http-status={httpStatusOf(submission.outcome) ?? ""}
      aria-live="polite"
      className="space-y-1"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">
          Submission #{submission.id}
        </span>
        <span className="text-xs text-muted-foreground">
          {modeLabel(submission.mode)}
        </span>
        <OutcomeBadge outcome={submission.outcome} />
      </div>
      <p className="text-xs text-muted-foreground break-all">
        {submission.url}
      </p>
      <p className="text-xs text-muted-foreground">
        Started {formatClock(submission.startedAt)}
        {duration !== undefined
          ? ` · finished ${formatClock(submission.startedAt + duration)} · ${formatDuration(
              duration
            )}`
          : ""}
      </p>
      {detail && <p className="text-xs">{detail}</p>}
    </div>
  );
}

function ResultContent({ content }: { content: StoredContent }) {
  const showJson =
    content.renderKind === "json" && isValidJSON(content.text);

  return (
    <div
      data-testid="result-content-value"
      data-render-kind={content.renderKind}
      className="text-sm"
    >
      {showJson ? (
        <JSONPretty data={JSON.parse(content.text)} style={{ fontSize: ".9em" }} />
      ) : (
        <div className="whitespace-pre-wrap">{content.text}</div>
      )}
    </div>
  );
}

type SubmissionResultProps = {
  submissions: Submission[];
  content: StoredContent | null;
  copied: boolean;
  onCopy: () => void;
};

export function SubmissionResult({
  submissions,
  content,
  copied,
  onCopy,
}: SubmissionResultProps) {
  const current = submissions.length
    ? submissions[submissions.length - 1]
    : undefined;
  const history = submissions.slice(0, -1).reverse();
  const hasContent = content !== null;
  const contentFromEarlierSubmission =
    content !== null && current !== undefined && content.submissionId !== current.id;
  const currentFailed =
    current !== undefined &&
    (current.outcome.kind === "server-rejected" ||
      current.outcome.kind === "network-error" ||
      current.outcome.kind === "malformed-response" ||
      current.outcome.kind === "cancelled");

  return (
    <section
      data-testid="result-panel"
      className="rounded p-4 mt-10 space-y-4 bg-secondary"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">Result</h2>
        {current ? (
          <OutcomeBadge outcome={current.outcome} />
        ) : (
          <span className="text-xs text-muted-foreground">
            No submissions yet
          </span>
        )}
      </div>

      {current ? (
        <SubmissionSummary submission={current} />
      ) : (
        <p className="text-sm text-muted-foreground">
          Submit a URL to see what happened to that request here.
        </p>
      )}

      <div className="bg-primary-foreground rounded p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="space-y-1">
            <h3 className="text-sm font-medium">Response content</h3>
            {hasContent ? (
              <p
                data-testid="result-content-source"
                data-submission-id={content.submissionId}
                className="text-xs text-muted-foreground"
              >
                {contentFromEarlierSubmission
                  ? `Showing the last successful result from submission #${content.submissionId} (${modeLabel(
                      content.mode
                    )}, received ${formatClock(
                      content.receivedAt
                    )}) — an earlier submission.${
                      currentFailed
                        ? " The latest submission did not produce new content."
                        : ""
                    }`
                  : `From submission #${content.submissionId} (${modeLabel(
                      content.mode
                    )}, received ${formatClock(content.receivedAt)}).`}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">
                {currentFailed
                  ? "No earlier successful content to fall back to."
                  : "Content will appear here once the workflow responds."}
              </p>
            )}
          </div>
          {hasContent && (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={onCopy}
              aria-label="Copy result"
            >
              {copied ? <Check size={16} /> : <Clipboard size={16} />}
            </Button>
          )}
        </div>
        {hasContent && (
          <div className="max-h-96 overflow-y-auto">
            <ResultContent content={content} />
          </div>
        )}
      </div>

      {history.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium">Earlier submissions</h3>
          <ol data-testid="submission-history" className="space-y-2">
            {history.map((submission) => (
              <li
                key={submission.id}
                data-testid="submission-history-item"
                data-submission-id={submission.id}
                data-outcome={submission.outcome.kind}
                className="flex flex-wrap items-center gap-2 rounded border border-border px-2 py-1"
              >
                <span className="text-xs font-medium">
                  #{submission.id}
                </span>
                <span className="text-xs text-muted-foreground">
                  {modeLabel(submission.mode)}
                </span>
                <OutcomeBadge outcome={submission.outcome} />
                <span className="text-xs text-muted-foreground">
                  {formatClock(submission.startedAt)}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}
