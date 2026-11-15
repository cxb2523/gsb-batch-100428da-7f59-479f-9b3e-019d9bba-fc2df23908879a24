package com.redabensaad.nbadraft_scraper.service;

import java.util.ArrayList;
import java.util.List;

/**
 * Per-team outcome of a batch run. Keeps enough detail to render a readable
 * summary distinguishing successes (with player counts), failures (with
 * reasons) and teams skipped because they were already finished.
 */
public class ScrapeReport {

    public enum Status {
        SUCCESS,
        FAILED,
        SKIPPED
    }

    public record TeamResult(
            String teamCode,
            Status status,
            int playerCount,
            int staleRecordsRemoved,
            String reason
    ) {
    }

    private final List<TeamResult> results = new ArrayList<>();

    public void addSuccess(String teamCode, int playerCount, int staleRecordsRemoved) {
        results.add(new TeamResult(teamCode, Status.SUCCESS, playerCount, staleRecordsRemoved, null));
    }

    public void addFailure(String teamCode, String reason, int partialPlayerCount,
                           int staleRecordsRemoved) {
        results.add(new TeamResult(teamCode, Status.FAILED, partialPlayerCount,
                staleRecordsRemoved, reason));
    }

    public void addSkipped(String teamCode) {
        results.add(new TeamResult(teamCode, Status.SKIPPED, 0, 0, null));
    }

    public List<TeamResult> getResults() {
        return List.copyOf(results);
    }

    public long successCount() {
        return results.stream().filter(r -> r.status() == Status.SUCCESS).count();
    }

    public long failureCount() {
        return results.stream().filter(r -> r.status() == Status.FAILED).count();
    }

    public long skippedCount() {
        return results.stream().filter(r -> r.status() == Status.SKIPPED).count();
    }

    public boolean hasFailures() {
        return failureCount() > 0;
    }

    /**
     * Renders a human-readable, per-team summary of the whole batch.
     */
    public String render() {
        StringBuilder sb = new StringBuilder();
        sb.append("\n=== SCRAPE SUMMARY ===\n");
        sb.append(String.format("Teams: %d total | %d succeeded | %d failed | %d skipped (resume)%n",
                results.size(), successCount(), failureCount(), skippedCount()));

        for (TeamResult result : results) {
            switch (result.status()) {
                case SUCCESS -> {
                    sb.append(String.format("[SUCCESS] %s — %d player(s) saved",
                            result.teamCode(), result.playerCount()));
                    if (result.staleRecordsRemoved() > 0) {
                        sb.append(String.format(" — %d stale partial record(s) from a previous "
                                + "failed run cleaned before re-scrape", result.staleRecordsRemoved()));
                    }
                    sb.append('\n');
                }
                case FAILED -> {
                    sb.append(String.format("[FAILED]  %s — reason: %s",
                            result.teamCode(), result.reason()));
                    sb.append(String.format(
                            "%n           %d partial player record(s) retained on disk; the team was "
                                    + "NOT marked finished, so on the next resume run these records are "
                                    + "removed and the team is re-scraped (no duplicates).%n",
                            result.playerCount()));
                }
                case SKIPPED -> sb.append(String.format(
                        "[SKIPPED] %s — already recorded in team-scraper.json, skipped by resume%n",
                        result.teamCode()));
            }
        }
        return sb.toString();
    }
}
