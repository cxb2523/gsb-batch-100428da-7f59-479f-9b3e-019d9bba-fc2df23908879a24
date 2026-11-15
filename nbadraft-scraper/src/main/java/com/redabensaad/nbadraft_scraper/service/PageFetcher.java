package com.redabensaad.nbadraft_scraper.service;

import org.jsoup.nodes.Document;

import java.io.IOException;

/**
 * Pluggable page fetching entry point.
 *
 * <p>The production implementation ({@link JsoupPageFetcher}) performs real HTTP
 * requests. Tests can inject a fake implementation serving local HTML pages, so
 * the whole scraping chain can be verified without any network access.
 */
@FunctionalInterface
public interface PageFetcher {
    Document fetch(String url) throws IOException;
}
