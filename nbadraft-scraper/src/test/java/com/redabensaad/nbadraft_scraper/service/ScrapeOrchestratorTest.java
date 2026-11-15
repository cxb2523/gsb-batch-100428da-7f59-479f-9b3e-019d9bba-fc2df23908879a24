package com.redabensaad.nbadraft_scraper.service;

import com.redabensaad.nbadraft_scraper.model.Player;
import com.redabensaad.nbadraft_scraper.util.DataSaver;
import com.redabensaad.nbadraft_scraper.util.HtmlParser;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * End-to-end tests of the batch scraping chain using local fake team pages —
 * no network access is performed.
 */
class ScrapeOrchestratorTest {

    private static final List<String> THREE_TEAMS = List.of("ATL", "BOS", "CHO");

    @TempDir
    Path tempDir;

    private FakePageFetcher pageFetcher;
    private DataSaver saver;
    private ScrapeOrchestrator orchestrator;

    @BeforeEach
    void setUp() {
        pageFetcher = new FakePageFetcher();
        HtmlParser parser = new HtmlParser();
        PlayerScraper playerScraper = new PlayerScraper(parser, pageFetcher);
        // Image downloads disabled and jitter removed: fully offline, fast tests.
        EspnImageService imageService = new EspnImageService(false);
        TeamScraper teamScraper = new TeamScraper(
                playerScraper, parser, imageService, pageFetcher, () -> { });
        saver = new DataSaver(tempDir.toString() + "/");
        orchestrator = new ScrapeOrchestrator(teamScraper, saver);
    }

    @Test
    @DisplayName("1) All teams succeed: every player is stored and every team is marked finished")
    void allTeamsSucceed() {
        pageFetcher.registerTeam("ATL", List.of("Hawk One", "Hawk Two"));
        pageFetcher.registerTeam("BOS", List.of("Celtic One"));
        pageFetcher.registerTeam("CHO", List.of("Hornet One", "Hornet Two", "Hornet Three"));

        ScrapeReport report = orchestrator.run(THREE_TEAMS, List.of());

        assertEquals(3, report.successCount());
        assertEquals(0, report.failureCount());

        List<String> finished = saver.loadList(ScrapeOrchestrator.TEAMS_FILE, String.class);
        assertEquals(THREE_TEAMS, finished);

        List<Player> players = saver.loadList(ScrapeOrchestrator.PLAYERS_FILE, Player.class);
        assertEquals(6, players.size());
        assertEquals(2, players.stream().filter(p -> "ATL".equals(p.teamId())).count());
        assertEquals(1, players.stream().filter(p -> "BOS".equals(p.teamId())).count());
        assertEquals(3, players.stream().filter(p -> "CHO".equals(p.teamId())).count());
        assertFalse(report.hasFailures());
    }

    @Test
    @DisplayName("2) A failing team request does not stop the batch and is not marked finished")
    void failingTeamDoesNotStopBatch() {
        pageFetcher.registerTeam("ATL", List.of("Hawk One"));
        pageFetcher.failTeamRequest("BOS");
        pageFetcher.registerTeam("CHO", List.of("Hornet One", "Hornet Two"));

        ScrapeReport report = orchestrator.run(THREE_TEAMS, List.of());

        // The following team is still processed and team order is preserved.
        assertEquals(List.of("ATL", "BOS", "CHO"), pageFetcher.teamCallOrder());

        assertEquals(2, report.successCount());
        assertEquals(1, report.failureCount());
        ScrapeReport.TeamResult failed = report.getResults().stream()
                .filter(r -> r.status() == ScrapeReport.Status.FAILED)
                .findFirst()
                .orElseThrow();
        assertEquals("BOS", failed.teamCode());
        assertTrue(failed.reason().contains("simulated connection failure"));

        // The failed team must NOT be recorded as finished.
        List<String> finished = saver.loadList(ScrapeOrchestrator.TEAMS_FILE, String.class);
        assertEquals(List.of("ATL", "CHO"), finished);
        assertFalse(finished.contains("BOS"));

        // Players of the teams around the failure are still on disk.
        List<Player> players = saver.loadList(ScrapeOrchestrator.PLAYERS_FILE, Player.class);
        assertEquals(3, players.size());
        assertTrue(players.stream().noneMatch(p -> "BOS".equals(p.teamId())));

        String rendered = report.render();
        assertTrue(rendered.contains("[SUCCESS] ATL"));
        assertTrue(rendered.contains("[FAILED]  BOS"));
        assertTrue(rendered.contains("[SUCCESS] CHO"));
    }

    @Test
    @DisplayName("3) Mid-team failure leaves partial players, and a resume run re-scrapes without duplicates")
    void resumeAfterMidTeamFailureDoesNotDuplicatePlayers() {
        pageFetcher.registerTeam("ATL", List.of("Hawk One"));
        // BOS has two rows; the second one blows up after the first player was saved.
        pageFetcher.registerTeam("BOS", List.of("Boston One", "Boston Two"));
        pageFetcher.explodePlayerPage("Boston Two");
        pageFetcher.registerTeam("CHO", List.of("Hornet One"));

        ScrapeReport firstRun = orchestrator.run(THREE_TEAMS, List.of());

        assertEquals(2, firstRun.successCount());
        ScrapeReport.TeamResult bostonFailure = firstRun.getResults().stream()
                .filter(r -> "BOS".equals(r.teamCode())).findFirst().orElseThrow();
        assertEquals(ScrapeReport.Status.FAILED, bostonFailure.status());
        assertEquals(1, bostonFailure.playerCount(), "the player parsed before the failure is retained");
        assertTrue(bostonFailure.reason().contains("simulated mid-team failure"));

        List<String> finishedAfterFirstRun =
                saver.loadList(ScrapeOrchestrator.TEAMS_FILE, String.class);
        assertEquals(List.of("ATL", "CHO"), finishedAfterFirstRun);

        List<Player> partial = saver.loadList(ScrapeOrchestrator.PLAYERS_FILE, Player.class);
        assertEquals(1, partial.stream().filter(p -> "BOS".equals(p.teamId())).count());
        assertEquals("Boston One", partial.stream()
                .filter(p -> "BOS".equals(p.teamId())).findFirst().orElseThrow().name());

        // Resume: the fake fetcher now succeeds for every page.
        FakePageFetcher healthyFetcher = new FakePageFetcher();
        healthyFetcher.registerTeam("ATL", List.of("Hawk One"));
        healthyFetcher.registerTeam("BOS", List.of("Boston One", "Boston Two"));
        healthyFetcher.registerTeam("CHO", List.of("Hornet One"));

        HtmlParser parser = new HtmlParser();
        TeamScraper healthyScraper = new TeamScraper(
                new PlayerScraper(parser, healthyFetcher), parser,
                new EspnImageService(false), healthyFetcher, () -> { });
        ScrapeOrchestrator resumeOrchestrator = new ScrapeOrchestrator(healthyScraper, saver);

        List<String> finishedBeforeResume =
                saver.loadList(ScrapeOrchestrator.TEAMS_FILE, String.class);
        ScrapeReport resumeReport = resumeOrchestrator.run(THREE_TEAMS, finishedBeforeResume);

        assertEquals(1, resumeReport.successCount(), "only BOS is re-scraped");
        assertEquals(2, resumeReport.skippedCount(), "ATL and CHO are skipped by resume");

        ScrapeReport.TeamResult bostonSuccess = resumeReport.getResults().stream()
                .filter(r -> "BOS".equals(r.teamCode())).findFirst().orElseThrow();
        assertEquals(ScrapeReport.Status.SUCCESS, bostonSuccess.status());
        assertEquals(2, bostonSuccess.playerCount());
        assertEquals(1, bostonSuccess.staleRecordsRemoved(),
                "the stale partial record was cleaned before re-scraping");

        // No duplicate players: exactly two BOS records, both still unique by name.
        List<Player> finalPlayers = saver.loadList(ScrapeOrchestrator.PLAYERS_FILE, Player.class);
        List<Player> bostonPlayers = finalPlayers.stream()
                .filter(p -> "BOS".equals(p.teamId())).toList();
        assertEquals(2, bostonPlayers.size());
        assertEquals(List.of("Boston One", "Boston Two"),
                bostonPlayers.stream().map(Player::name).sorted().toList());
        assertEquals(finalPlayers.size(), finalPlayers.stream().map(Player::name).distinct().count());

        // Resume appends BOS at the end of the append-only finished list;
        // order is irrelevant, what matters is that all three teams are present.
        List<String> finalFinished = saver.loadList(ScrapeOrchestrator.TEAMS_FILE, String.class);
        assertEquals(new java.util.HashSet<>(THREE_TEAMS), new java.util.HashSet<>(finalFinished));
        assertEquals(THREE_TEAMS.size(), finalFinished.size());

        String rendered = resumeReport.render();
        assertTrue(rendered.contains("[SKIPPED] ATL"));
        assertTrue(rendered.contains("[SUCCESS] BOS"));
        assertTrue(rendered.contains("[SKIPPED] CHO"));
    }
}
