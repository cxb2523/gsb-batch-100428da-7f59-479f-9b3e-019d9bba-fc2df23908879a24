package com.redabensaad.nbadraft_scraper.service;

import com.redabensaad.nbadraft_scraper.exception.ScraperException;
import com.redabensaad.nbadraft_scraper.model.Player;
import com.redabensaad.nbadraft_scraper.util.DataSaver;

import java.io.IOException;
import java.util.List;

/**
 * Drives a full batch over every team.
 *
 * <p>Failure isolation rules:
 * <ul>
 *     <li>a single team failing never aborts the batch — the following teams
 *     are still processed;</li>
 *     <li>a failed team is never appended to {@code team-scraper.json}, so the
 *     next resume run picks it up again;</li>
 *     <li>players parsed before a mid-team failure are kept on disk (they are
 *     flushed immediately), but before any team is (re-)scraped its stale
 *     records are removed, which prevents duplicate players on resume.</li>
 * </ul>
 */
public class ScrapeOrchestrator {
    public static final String PLAYERS_FILE = "players-data.json";
    public static final String TEAMS_FILE = "team-scraper.json";

    private final TeamScraper teamScraper;
    private final DataSaver saver;

    public ScrapeOrchestrator(TeamScraper teamScraper, DataSaver saver) {
        this.teamScraper = teamScraper;
        this.saver = saver;
    }

    /**
     * Runs the batch over {@code allTeams} in order.
     *
     * @param finishedTeams teams already present in {@code team-scraper.json};
     *                      they are skipped (resume behaviour)
     */
    public ScrapeReport run(List<String> allTeams, List<String> finishedTeams) {
        ScrapeReport report = new ScrapeReport();

        for (String teamCode : allTeams) {
            if (finishedTeams.contains(teamCode)) {
                System.out.printf("%n--- SKIPPING (already finished): %s ---%n", teamCode);
                report.addSkipped(teamCode);
                continue;
            }

            System.out.printf("%n--- STARTING SCRAPING: %s ---%n", teamCode);

            // Clean partial records that a previous failed run may have left.
            // Successful teams are in finishedTeams and never reach this point,
            // so this never deletes data of a completed team.
            int staleRemoved = saver.removePlayersByTeam(PLAYERS_FILE, teamCode);

            int[] savedCount = {0};
            try {
                teamScraper.scrapeTeam(teamCode, player -> persistPlayer(teamCode, player, savedCount));
            } catch (ScraperException e) {
                System.err.printf("Team %s failed: %s%n", teamCode, e.getMessage());
                report.addFailure(teamCode, describe(e), savedCount[0], staleRemoved);
                continue;
            } catch (RuntimeException e) {
                System.err.printf("Team %s failed unexpectedly: %s%n", teamCode, e.getMessage());
                report.addFailure(teamCode, describe(e), savedCount[0], staleRemoved);
                continue;
            }

            // Only record the team as finished once scraping fully succeeded.
            try {
                saver.addObject(TEAMS_FILE, teamCode);
                saver.saveObjects();
            } catch (RuntimeException e) {
                // The team list itself could not be persisted: treat the team as
                // failed so a resume run re-scrapes it (its partial records are
                // cleaned first, so no duplicate players are created).
                System.err.printf("Team %s could not be marked finished: %s%n",
                        teamCode, e.getMessage());
                report.addFailure(teamCode, "failed to record team as finished: "
                        + describe(e), savedCount[0], staleRemoved);
                continue;
            }

            System.out.printf("%nTeam %s completed — progress saved.%n", teamCode);
            report.addSuccess(teamCode, savedCount[0], staleRemoved);
        }

        return report;
    }

    private void persistPlayer(String teamCode, Player player, int[] savedCount) {
        try {
            saver.appendObject(PLAYERS_FILE, player);
            savedCount[0]++;
        } catch (IOException e) {
            // A write failure must surface as a team failure rather than being
            // silently swallowed, otherwise the team could be marked finished
            // while missing players.
            throw new ScraperException("Unable to persist player of team " + teamCode,
                    teamCode, e);
        }
    }

    private String describe(Throwable throwable) {
        String message = throwable.getMessage();
        Throwable cause = throwable.getCause();
        if (message != null && cause != null && cause.getMessage() != null) {
            return message + " (" + cause.getClass().getSimpleName()
                    + ": " + cause.getMessage() + ")";
        }
        if (message != null) return message;
        return throwable.getClass().getSimpleName();
    }
}
