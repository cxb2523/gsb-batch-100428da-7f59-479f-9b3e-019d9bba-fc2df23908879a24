package com.redabensaad.nbadraft_scraper.service;

import com.redabensaad.nbadraft_scraper.config.ScraperConfig;
import org.jsoup.Jsoup;
import org.jsoup.nodes.Document;

import java.io.IOException;

/**
 * Default {@link PageFetcher} backed by Jsoup, using the scraper's standard
 * user agent and timeout.
 */
public class JsoupPageFetcher implements PageFetcher {
    @Override
    public Document fetch(String url) throws IOException {
        return Jsoup.connect(url)
                .userAgent(ScraperConfig.USER_AGENT)
                .timeout(ScraperConfig.TIMEOUT_MS)
                .get();
    }
}
