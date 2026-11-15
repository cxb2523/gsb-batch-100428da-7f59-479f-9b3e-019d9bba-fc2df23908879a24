package com.redabensaad.nbadraft_scraper.service;

import com.redabensaad.nbadraft_scraper.config.ScraperConfig;
import org.jsoup.Jsoup;
import org.jsoup.nodes.Document;

import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * In-memory replacement for {@link JsoupPageFetcher}: serves pre-registered
 * local HTML pages instead of performing HTTP requests. It can simulate both a
 * team-page request failure (IOException) and a failure halfway through a team
 * (a runtime error while enriching a specific player).
 */
class FakePageFetcher implements PageFetcher {
    private final Map<String, String> pages = new HashMap<>();
    private final Set<String> failingTeamUrls = new HashSet<>();
    private final Set<String> explodingPlayerUrls = new HashSet<>();
    private final List<String> teamCallOrder = new ArrayList<>();

    void registerTeam(String teamCode, List<String> playerNames) {
        pages.put(teamUrl(teamCode), buildTeamPage(playerNames));
        for (String name : playerNames) {
            pages.put(playerUrl(name), "<html><body><div>fake player page</div></body></html>");
        }
    }

    /** Simulates a failed HTTP request for a team contract page. */
    void failTeamRequest(String teamCode) {
        failingTeamUrls.add(teamUrl(teamCode));
    }

    /** Simulates a failure while processing one player row of a team. */
    void explodePlayerPage(String playerName) {
        explodingPlayerUrls.add(playerUrl(playerName));
    }

    List<String> teamCallOrder() {
        return List.copyOf(teamCallOrder);
    }

    @Override
    public Document fetch(String url) throws IOException {
        if (url.contains("/contracts/")) {
            String teamCode = url.substring(url.lastIndexOf('/') + 1).replace(".html", "");
            teamCallOrder.add(teamCode);
            if (failingTeamUrls.contains(url)) {
                throw new IOException("simulated connection failure for " + url);
            }
        }

        if (explodingPlayerUrls.contains(url)) {
            throw new RuntimeException("simulated mid-team failure while enriching player");
        }

        String html = pages.get(url);
        if (html == null) {
            throw new IOException("no fake page registered for " + url);
        }
        return Jsoup.parse(html);
    }

    private static String buildTeamPage(List<String> playerNames) {
        StringBuilder rows = new StringBuilder();
        for (String name : playerNames) {
            rows.append("""
                    <tr>
                      <th data-stat="player"><a href="/players/%s.html">%s</a></th>
                      <td data-stat="age_today">30</td>
                      <td data-stat="remain_gtd">$10,000,000</td>
                      <td data-stat="y2">5000000</td>
                    </tr>
                    """.formatted(playerSlug(name), name));
        }
        return "<html><body><table id=\"contracts\"><tbody>" + rows + "</tbody></table></body></html>";
    }

    private static String playerSlug(String name) {
        return name.toLowerCase(Locale.ROOT).replace(" ", "");
    }

    private static String teamUrl(String teamCode) {
        return ScraperConfig.BASE_URL + "contracts/" + teamCode + ".html";
    }

    private static String playerUrl(String name) {
        return ScraperConfig.BASE_URL + "/players/" + playerSlug(name) + ".html";
    }
}
