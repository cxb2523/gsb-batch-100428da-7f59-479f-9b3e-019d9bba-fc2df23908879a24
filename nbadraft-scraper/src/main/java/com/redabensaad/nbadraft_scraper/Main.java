package com.redabensaad.nbadraft_scraper;

import com.redabensaad.nbadraft_scraper.service.EspnImageService;
import com.redabensaad.nbadraft_scraper.service.PlayerScraper;
import com.redabensaad.nbadraft_scraper.service.ScrapeOrchestrator;
import com.redabensaad.nbadraft_scraper.service.ScrapeReport;
import com.redabensaad.nbadraft_scraper.service.TeamScraper;
import com.redabensaad.nbadraft_scraper.util.DataSaver;
import com.redabensaad.nbadraft_scraper.util.HtmlParser;

import java.util.ArrayList;
import java.util.List;
import java.util.Scanner;

public class Main {
    private static final List<String> ALL_TEAMS = List.of(
            "ATL", "BOS", "BRK", "CHO", "CHI", "CLE", "DAL", "DEN", "DET", "GSW",
            "HOU", "IND", "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK",
            "OKC", "ORL", "PHI", "PHO", "POR", "SAC", "SAS", "TOR", "UTA", "WAS"
    );

    private static final Scanner SCANNER = new Scanner(System.in);

    public static void main(String[] args) {

        HtmlParser parser = new HtmlParser();
        EspnImageService espnImageService = new EspnImageService();
        PlayerScraper playerScraper = new PlayerScraper(parser);
        TeamScraper teamScraper = new TeamScraper(playerScraper, parser, espnImageService);
        DataSaver saver = new DataSaver();

        System.out.println("=== WELCOME TO THE NBA DATA SCRAPER ===");

        if (askScrapeLogos()) {
            scrapeLogos(espnImageService);
            System.out.println("\nScraping completed successfully!");
        } else {
            scrapePlayers(teamScraper, saver);
        }
    }

    private static void scrapeLogos(EspnImageService espnImageService) {
        for (String teamCode : ALL_TEAMS) {
            System.out.printf("--- FETCHING LOGO: %s ---\n", teamCode);
            espnImageService.getTeamLogo(teamCode.toLowerCase());
        }
    }

    private static void scrapePlayers(TeamScraper teamScraper, DataSaver saver) {
        List<String> finishedTeams = resolveFinishedTeams(saver);
        ScrapeOrchestrator orchestrator = new ScrapeOrchestrator(teamScraper, saver);

        // A single team failure is contained by the orchestrator: the batch
        // keeps going and failed teams stay out of team-scraper.json so that
        // the next resume run re-scrapes them.
        ScrapeReport report = orchestrator.run(ALL_TEAMS, finishedTeams);

        System.out.println(report.render());
        if (report.hasFailures()) {
            System.out.println("Scraping finished with failures — re-run and choose 'resume' "
                    + "to retry the failed teams.");
        } else {
            System.out.println("Scraping completed successfully!");
        }
    }

    private static List<String> resolveFinishedTeams(DataSaver saver) {
        if (askResume()) {
            List<String> finished = saver.loadList(
                    ScrapeOrchestrator.TEAMS_FILE, String.class);

            List<String> remaining = new ArrayList<>(ALL_TEAMS);
            remaining.removeAll(finished);

            System.out.printf("Resuming: %d/%d teams remaining.\n",
                    remaining.size(), ALL_TEAMS.size());
            return finished;
        }

        System.out.printf("Full scraping restart (%d teams).\n", ALL_TEAMS.size());
        return List.of();
    }

    private static boolean askResume() {
        while (true) {
            System.out.print("Resume previous scraping progress? (y/n): ");
            String input = SCANNER.nextLine().trim().toLowerCase();
            if (input.equals("y")) return true;
            if (input.equals("n")) return false;
            System.out.println("Invalid input, please enter 'y' or 'n'.");
        }
    }

    private static boolean askScrapeLogos() {
        while (true) {
            System.out.print("What do you want to do?\n1 - Fetch team logos\n2 - Scrape player data\nChoice (1/2): ");
            String input = SCANNER.nextLine().trim();
            if (input.equals("1")) return true;
            if (input.equals("2")) return false;
            System.out.println("Invalid input, please enter '1' or '2'.");
        }
    }
}
