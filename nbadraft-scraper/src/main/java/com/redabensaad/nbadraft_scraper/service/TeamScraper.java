package com.redabensaad.nbadraft_scraper.service;

import com.redabensaad.nbadraft_scraper.config.ScraperConfig;
import com.redabensaad.nbadraft_scraper.exception.ScraperException;
import com.redabensaad.nbadraft_scraper.model.Contract;
import com.redabensaad.nbadraft_scraper.model.Option;
import com.redabensaad.nbadraft_scraper.model.Player;
import com.redabensaad.nbadraft_scraper.model.PlayerStats;
import com.redabensaad.nbadraft_scraper.util.HtmlParser;
import com.redabensaad.nbadraft_scraper.util.Jitter;
import org.jsoup.nodes.Document;
import org.jsoup.nodes.Element;
import org.jsoup.select.Elements;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.function.Consumer;

public class TeamScraper {
    private static final String ROW_SELECTOR =
            "table#contracts tbody tr:not(.partial_table):not(.thead)";

    private final PlayerScraper playerScraper;
    private final HtmlParser parser;
    private final EspnImageService espnImageService;
    private final PageFetcher pageFetcher;
    private final Runnable jitter;

    public TeamScraper(PlayerScraper playerScraper, HtmlParser parser, EspnImageService espnImageService) {
        this(playerScraper, parser, espnImageService,
                new JsoupPageFetcher(), Jitter::applyJitter);
    }

    /**
     * Test-friendly constructor: the page fetcher can be swapped for local fake
     * pages and the jitter can be replaced by a no-op to keep tests fast.
     */
    public TeamScraper(PlayerScraper playerScraper, HtmlParser parser, EspnImageService espnImageService,
                       PageFetcher pageFetcher, Runnable jitter) {
        this.playerScraper = playerScraper;
        this.parser = parser;
        this.espnImageService = espnImageService;
        this.pageFetcher = pageFetcher;
        this.jitter = jitter;
    }

    public List<Player> scrapeTeam(String teamCode) {
        List<Player> players = new ArrayList<>();
        scrapeTeam(teamCode, players::add);
        return players;
    }

    /**
     * Scrapes one team and passes every successfully parsed player to
     * {@code playerHandler} as soon as it is available, so partial results can
     * be persisted before a mid-team failure.
     *
     * @throws ScraperException if the team page cannot be fetched or any row
     *                          fails while scraping the team
     */
    public void scrapeTeam(String teamCode, Consumer<Player> playerHandler) {
        String url = ScraperConfig.BASE_URL + "contracts/" + teamCode + ".html";
        int fetched = 0;

        try {
            Document doc = pageFetcher.fetch(url);
            Elements rows = doc.select(ROW_SELECTOR);

            for (Element row : rows) {
                Optional<Player> player = parseRow(row, teamCode);
                if (player.isPresent()) {
                    playerHandler.accept(player.get());
                    fetched++;
                }
                jitter.run();
            }

        } catch (ScraperException e) {
            throw e;
        } catch (IOException e) {
            throw new ScraperException("Unable to access team " + teamCode, teamCode, e);
        } catch (RuntimeException e) {
            // Failures while parsing/enriching a row also abort the team run.
            throw new ScraperException("Unexpected error while scraping team " + teamCode, teamCode, e);
        }

        System.out.printf("Team %s: %d players fetched\n", teamCode, fetched);
    }

    private Optional<Player> parseRow(Element row, String teamCode) {
        Contract contract = parseContract(row);
        if (contract == null) return Optional.empty();

        Element linkEl = row.selectFirst("th[data-stat=player] a");
        if (linkEl == null) return Optional.empty();

        String name = row.select("th[data-stat=player]").text();
        int age = parser.parseIntStat(row.select("td[data-stat=age_today]").text());
        String playerPath = linkEl.attr("href");
        String fileName = extractFileName(playerPath).replace("jpg", "png");

        System.out.printf("Traitement : %s [%s] \n", name, teamCode);

        PlayerStats stats = playerScraper.fetchStats(playerPath).orElse(null);

        espnImageService.getPlayerHeadShot(name, "players/" + teamCode, fileName);

        return Optional.of(new Player(name, teamCode, age, contract, stats, fileName));
    }

    private String extractFileName(String playerPath) {
        return playerPath
                .substring(playerPath.lastIndexOf('/') + 1)
                .replace(".html", ".jpg");
    }

    private Contract parseContract(Element row) {
        String nextSeasonText = row.select("td[data-stat=y2]").text();
        String capHitText = row.select("td[data-stat=remain_gtd]").text();

        if (nextSeasonText.isEmpty() || capHitText.isEmpty()) return null;

        long capHit = 0;
        for (int i = 2; ; i++) {
            Element seasonElement = row.selectFirst("td[data-stat=y" + i + "]");
            if (seasonElement == null || seasonElement.text().isEmpty()) break;
            capHit += parser.parseIntStat(seasonElement.text());
        }

        Option option = resolveContractOption(row);
        int seasonsRemaining = countRemainingSeasons(row);
        long nextSeason = parser.parseIntStat(nextSeasonText);

        return new Contract(capHit, seasonsRemaining, nextSeason, option);
    }

    private Option resolveContractOption(Element row) {
        if (row.selectFirst("td.salary-pl") != null) return Option.PLAYER;
        if (row.selectFirst("td.salary-tm") != null) return Option.TEAM;
        return Option.NONE;
    }

    private int countRemainingSeasons(Element row) {
        int count = 1;
        for (int i = 3; ; i++) {
            Element seasonElement = row.selectFirst("td[data-stat=y" + i + "]");
            if (seasonElement == null || seasonElement.text().isEmpty()) break;
            count++;
        }
        return count;
    }
}
