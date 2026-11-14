---
"@flyrank/flyscrape": minor
---

Add an opt-in `includeSitemapSummary` option to `fetchSitemapUrls` (and pass-through in `crawlFromSitemap`) that reports sitemap traversal coverage: child sitemaps fetched, cross-origin skipped, truncation by `maxUrls`/`maxSitemaps`, and the outcome of each discovered child sitemap
