package com.redabensaad.nbadraft_scraper.util;

import com.redabensaad.nbadraft_scraper.model.Contract;
import com.redabensaad.nbadraft_scraper.model.Option;
import com.redabensaad.nbadraft_scraper.model.Player;
import com.redabensaad.nbadraft_scraper.model.PlayerStats;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DataSaverTest {

    private static final String FILE = "players-data.json";

    @TempDir
    Path tempDir;

    private DataSaver saver;

    @BeforeEach
    void setUp() {
        saver = new DataSaver(tempDir.toString() + "/");
    }

    @Test
    void appendObjectPersistsImmediatelyAndAccumulatesAcrossCalls() throws Exception {
        saver.appendObject(FILE, player("ATL", "Hawk One"));
        saver.appendObject(FILE, player("ATL", "Hawk Two"));
        saver.appendObject(FILE, player("BOS", "Celtic One"));

        List<Player> stored = saver.loadList(FILE, Player.class);
        assertEquals(3, stored.size());
        assertEquals(List.of("Hawk One", "Hawk Two", "Celtic One"),
                stored.stream().map(Player::name).toList());
    }

    @Test
    void removePlayersByTeamOnlyDropsTheTargetTeamAndIsIdempotent() throws Exception {
        saver.appendObject(FILE, player("ATL", "Hawk One"));
        saver.appendObject(FILE, player("BOS", "Boston One"));
        saver.appendObject(FILE, player("BOS", "Boston Two"));
        saver.appendObject(FILE, player("CHO", "Hornet One"));

        int removed = saver.removePlayersByTeam(FILE, "BOS");

        assertEquals(2, removed);
        List<Player> remaining = saver.loadList(FILE, Player.class);
        assertEquals(2, remaining.size());
        assertTrue(remaining.stream().noneMatch(p -> "BOS".equals(p.teamId())));

        // Running the cleanup again removes nothing and keeps the file stable.
        assertEquals(0, saver.removePlayersByTeam(FILE, "BOS"));
        assertEquals(2, saver.loadList(FILE, Player.class).size());
    }

    @Test
    void reScrapingAfterCleanupProducesNoDuplicates() throws Exception {
        // Simulate a previous failed run that left one partial BOS record.
        saver.appendObject(FILE, player("BOS", "Boston One"));

        // Resume run first cleans the team, then writes its full fresh roster.
        int cleaned = saver.removePlayersByTeam(FILE, "BOS");
        assertEquals(1, cleaned);
        saver.appendObject(FILE, player("BOS", "Boston One"));
        saver.appendObject(FILE, player("BOS", "Boston Two"));

        List<Player> bostonPlayers = saver.loadList(FILE, Player.class).stream()
                .filter(p -> "BOS".equals(p.teamId()))
                .toList();
        assertEquals(2, bostonPlayers.size());
        assertFalse(bostonPlayers.stream().map(Player::name)
                .anyMatch(name -> bostonPlayers.stream()
                        .filter(p -> p.name().equals(name)).count() > 1));
    }

    private static Player player(String teamId, String name) {
        return new Player(name, teamId, 25,
                new Contract(1_000_000, 1, 1_000_000, Option.NONE),
                new PlayerStats(10, 5, 4, 80, "SG"),
                name.replace(" ", "") + ".png");
    }
}
