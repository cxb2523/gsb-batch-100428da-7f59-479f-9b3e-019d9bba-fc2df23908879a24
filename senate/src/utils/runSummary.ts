import type { PipelineStats } from '../scheduler/pipeline.js';

// Written to the run's default key-value store under 'RUN_SUMMARY' on every
// successful run. `truncated: true` is the machine-readable signal that the
// dataset is missing rows because the run's max total charge was reached —
// the run itself still ends SUCCEEDED.
export function buildRunSummary(stats: PipelineStats): Record<string, unknown> {
  return {
    truncated: stats.truncated,
    reason: stats.truncated ? stats.truncationReason ?? 'max_total_charge_reached' : null,
    rowsEmitted: stats.rowsEmitted,
    rowsNotEmitted: stats.rowsNotEmitted,
    // Rows are emitted newest filing first, so a truncated run is missing the
    // OLDEST filings. lastFilingDate is the filing date of the last row
    // written; a filing straddling the cap may be only partly written, so
    // re-run with toDate = lastFilingDate (that day overlaps — see README)
    // and a higher max charge to pick up the rest.
    lastFilingDate: stats.lastFilingDate,
    lastFilingId: stats.lastFilingId,
    windowFrom: stats.windowFrom,
    windowTo: stats.windowTo,
    // Rows removed as exact cross-filing duplicates (same content_hash,
    // different filing) — before anything was written or billed.
    duplicatesCollapsed: stats.duplicatesRemoved,
    duplicatesRemoved: stats.duplicatesRemoved, // same value, kept for existing consumers
    skippedByMemberCount: stats.skippedByMemberCount,
    skippedByTransactionDateCount: stats.skippedByTransactionDateCount,
    filteredByTickerCount: stats.filteredByTickerCount,
    filteredByTransactionDateCount: stats.filteredByTransactionDateCount,
    // Placeholder rows (scanned_unparsed / parse_failed / fetch_failed) NOT
    // emitted because a tickers or transaction-date filter was set — their
    // content is unknown, so the filter can't be evaluated on them.
    placeholdersWithheld: stats.placeholdersExcludedCount,
    placeholdersExcludedCount: stats.placeholdersExcludedCount, // same value, kept for existing consumers
    // Per-filing outcome breakdown for this run. The four filing kinds below
    // are mutually exclusive: every recognized filing contributes exactly one.
    // unknownDocTypeCount is separate — those listing rows had no recognizable
    // filing (no /ptr/ or /paper/ link), so they have no filing_id and are
    // never written as placeholders.
    electronicPtrCount: stats.electronicPtrCount ?? 0,
    paperCount: stats.paperCount ?? 0,
    emptyPtrCount: stats.emptyPtrCount ?? 0,
    fetchFailedCount: stats.fetchFailedCount ?? 0,
    unknownDocTypeCount: stats.unknownDocTypeCount ?? 0,
    unknownDocTypeExamples: stats.unknownDocTypeExamples ?? [],
    // Per-filing index behind the counts above: filing_id, detail URL, and
    // (for fetch_failed) the error message — so a run's failures can be
    // located directly instead of re-reading the dataset.
    outcomes: stats.outcomes ?? [],
    // False when the listing stopped early; the filings collected before the
    // failure were still fetched and counted, but the run is incomplete.
    listingComplete: stats.listingComplete ?? true,
    incomplete: stats.incomplete ?? false,
  };
}
