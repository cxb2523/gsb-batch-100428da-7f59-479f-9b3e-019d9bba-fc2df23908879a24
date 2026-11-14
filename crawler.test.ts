import { describe, expect, test, afterAll, beforeAll } from "bun:test";
import { mapWebsite, crawlWebsite, classifySitemapDocument, MAX_SITEMAP_DEPTH } from "./crawler.js";

describe("Autonomous Web Crawler & Mapper (crawler.ts)", () => {
  let server: ReturnType<typeof Bun.serve>;
  let serverUrl: string;
  const requestLog: Array<{ path: string; start: number; end?: number }> = [];
  let inFlight = 0;
  let maxInFlight = 0;

  beforeAll(() => {
    process.env.LOOKACRAWLER_ALLOW_LOCAL = "true";
    server = Bun.serve({
      port: 0,
      async fetch(req) {
        const url = new URL(req.url);

        if (url.pathname.startsWith("/pool/")) {
          const entry: { path: string; start: number; end?: number } = { path: url.pathname, start: performance.now() };
          requestLog.push(entry);
          inFlight++;
          maxInFlight = Math.max(maxInFlight, inFlight);
          if (url.pathname === "/pool/slow") await Bun.sleep(1500);
          inFlight--;
          entry.end = performance.now();
          const links =
            url.pathname === "/pool/hub"
              ? ["slow", "f1", "f2", "f3"].map((p) => `<a href="/pool/${p}">${p}</a>`).join("")
              : "";
          return new Response(
            `<!DOCTYPE html><html><body><h1>${url.pathname}</h1><p>Pool test page content.</p>${links}</body></html>`,
            { headers: { "Content-Type": "text/html" } }
          );
        }

        if (url.pathname === "/robots.txt") {
          return new Response(
            `User-agent: *\nAllow: /\nSitemap: ${serverUrl}/sitemap.xml\n`,
            { headers: { "Content-Type": "text/plain" } }
          );
        }

        if (url.pathname === "/sitemap.xml") {
          return new Response(
            `<?xml version="1.0" encoding="UTF-8"?>
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url>
                <loc>${serverUrl}/page1</loc>
                <lastmod>2026-09-01</lastmod>
              </url>
              <url>
                <loc>${serverUrl}/page2</loc>
                <lastmod>2026-09-02</lastmod>
              </url>
              <url>
                <loc>${serverUrl}/private/admin</loc>
              </url>
              <url>
                <loc>https://external-domain.com/outside</loc>
              </url>
            </urlset>`,
            { headers: { "Content-Type": "application/xml" } }
          );
        }

        if (url.pathname === "/") {
          return new Response(
            `<!DOCTYPE html>
            <html>
              <body>
                <h1>Home Page</h1>
                <a href="/about">About Us</a>
                <a href="/contact">Contact</a>
                <a href="https://external-domain.com/link">External Link</a>
              </body>
            </html>`,
            { headers: { "Content-Type": "text/html" } }
          );
        }

        if (url.pathname === "/page1") {
          return new Response(
            `<!DOCTYPE html>
            <html>
              <body>
                <h1>Page 1 Content</h1>
                <p>Welcome to page one of the crawl test suite.</p>
                <a href="/page1/subpage">Page 1 Subpage</a>
              </body>
            </html>`,
            { headers: { "Content-Type": "text/html" } }
          );
        }

        if (url.pathname === "/page1/subpage") {
          return new Response(
            `<!DOCTYPE html>
            <html>
              <body>
                <h1>Subpage of Page 1</h1>
                <p>Deep nested subpage content.</p>
              </body>
            </html>`,
            { headers: { "Content-Type": "text/html" } }
          );
        }

        if (url.pathname === "/page2") {
          return new Response(
            `<!DOCTYPE html>
            <html>
              <body>
                <h1>Page 2</h1>
                <p>Content of page two.</p>
              </body>
            </html>`,
            { headers: { "Content-Type": "text/html" } }
          );
        }

        if (url.pathname === "/about") {
          return new Response(
            `<!DOCTYPE html>
            <html>
              <body>
                <h1>About Us</h1>
                <p>We are building LookaCrawler.</p>
              </body>
            </html>`,
            { headers: { "Content-Type": "text/html" } }
          );
        }

        if (url.pathname === "/contact") {
          return new Response(
            `<!DOCTYPE html>
            <html>
              <body>
                <h1>Contact Page</h1>
                <p>Contact information.</p>
              </body>
            </html>`,
            { headers: { "Content-Type": "text/html" } }
          );
        }

        return new Response("Not Found", { status: 404 });
      },
    });

    serverUrl = `http://localhost:${server.port}`;
  });

  afterAll(() => {
    delete process.env.LOOKACRAWLER_ALLOW_LOCAL;
    server.stop(true);
  });

  describe("mapWebsite", () => {
    test("should discover URLs declared in sitemap.xml referenced by robots.txt", async () => {
      const result = await mapWebsite({
        url: serverUrl,
        maxUrls: 50,
      });

      expect(result.host).toBe("localhost");
      expect(result.sitemapsFound.length).toBeGreaterThan(0);
      const urls = result.urls.map((u) => u.url);
      expect(urls).toContain(`${serverUrl}/page1`);
      expect(urls).toContain(`${serverUrl}/page2`);
      // Should filter out external domain
      expect(urls).not.toContain("https://external-domain.com/outside");
    });

    test("should filter discovered URLs using excludePatterns and includePatterns", async () => {
      const result = await mapWebsite({
        url: serverUrl,
        includePatterns: ["page"],
        excludePatterns: ["admin"],
        maxUrls: 10,
      });

      const urls = result.urls.map((u) => u.url);
      expect(urls).toContain(`${serverUrl}/page1`);
      expect(urls).toContain(`${serverUrl}/page2`);
      expect(urls).not.toContain(`${serverUrl}/private/admin`);
    });

    test("should respect maxUrls boundary", async () => {
      const result = await mapWebsite({
        url: serverUrl,
        maxUrls: 1,
      });

      expect(result.urls.length).toBe(1);
    });
  });

  describe("sitemap indexes", () => {
    let indexServer: ReturnType<typeof Bun.serve>;
    let indexUrl: string;
    const requests: string[] = [];

    beforeAll(() => {
      indexServer = Bun.serve({
        port: 0,
        fetch(req) {
          const url = new URL(req.url);
          requests.push(url.pathname);
          const xml = (body: string) =>
            new Response(`<?xml version="1.0" encoding="UTF-8"?>\n${body}`, {
              headers: { "Content-Type": "application/xml" },
            });

          if (url.pathname === "/robots.txt") {
            return new Response(`User-agent: *\nAllow: /\nSitemap: ${indexUrl}/sitemap-index.xml\n`, {
              headers: { "Content-Type": "text/plain" },
            });
          }

          if (url.pathname === "/sitemap-index.xml") {
            return xml(
              `<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                <sitemap><loc>${indexUrl}/pages-a.xml</loc></sitemap>
                <sitemap><loc>${indexUrl}/pages-broken.xml</loc></sitemap>
                <sitemap><loc>${indexUrl}/pages-junk.xml</loc></sitemap>
                <sitemap><loc>${indexUrl}/sitemap-self.xml</loc></sitemap>
              </sitemapindex>`
            );
          }

          // A 200 response whose body is not a sitemap at all.
          if (url.pathname === "/pages-junk.xml") {
            return new Response("<!DOCTYPE html><html><body>Maintenance</body></html>", {
              headers: { "Content-Type": "text/html" },
            });
          }

          if (url.pathname === "/pages-a.xml") {
            return xml(
              `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                <url><loc>${indexUrl}/page-a</loc><lastmod>2026-10-01</lastmod></url>
                <url><loc>${indexUrl}/page-b</loc><lastmod>2026-10-02</lastmod></url>
                <url><loc>https://external-domain.com/outside</loc></url>
              </urlset>`
            );
          }

          if (url.pathname === "/pages-broken.xml") {
            return new Response("upstream exploded", { status: 500 });
          }

          // An index listing only itself: following it must stop at the depth cap.
          if (url.pathname === "/sitemap-self.xml") {
            return xml(
              `<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                <sitemap><loc>${indexUrl}/sitemap-self.xml</loc></sitemap>
              </sitemapindex>`
            );
          }

          return new Response("Not Found", { status: 404 });
        },
      });
      indexUrl = `http://localhost:${indexServer.port}`;
    });

    afterAll(() => {
      indexServer.stop(true);
    });

    test("recurses into child sitemaps and records an outcome per sitemap address", async () => {
      requests.length = 0;
      const result = await mapWebsite({ url: indexUrl, maxUrls: 200 });

      const urls = result.urls.map((u) => u.url);
      expect(urls).toContain(`${indexUrl}/page-a`);
      expect(urls).toContain(`${indexUrl}/page-b`);
      // Same-host filter still applies to pages inside child sitemaps.
      expect(urls).not.toContain("https://external-domain.com/outside");
      const pageA = result.urls.find((u) => u.url === `${indexUrl}/page-a`);
      expect(pageA?.lastmod).toBe("2026-10-01");

      const indexOutcome = result.sitemapOutcomes.find((o) => o.url === `${indexUrl}/sitemap-index.xml`);
      expect(indexOutcome).toMatchObject({ kind: "index", status: "ok", pagesFound: 0 });

      const pagesOutcome = result.sitemapOutcomes.find((o) => o.url === `${indexUrl}/pages-a.xml`);
      expect(pagesOutcome).toMatchObject({ kind: "urlset", status: "ok", pagesFound: 2 });

      const brokenOutcome = result.sitemapOutcomes.find((o) => o.url === `${indexUrl}/pages-broken.xml`);
      expect(brokenOutcome?.status).toBe("http_error");
      expect(brokenOutcome?.error).toContain("500");

      // The broken child did not stop the healthy ones from being collected.
      expect(result.totalDiscovered).toBeGreaterThanOrEqual(2);
    });

    test("reports a sitemap body that is not a sitemap as parse_error", async () => {
      const result = await mapWebsite({ url: indexUrl, maxUrls: 200 });

      const junk = result.sitemapOutcomes.find((o) => o.url === `${indexUrl}/pages-junk.xml`);
      expect(junk).toMatchObject({ kind: "unknown", status: "parse_error", pagesFound: 0 });
      expect(junk?.error).toBeTruthy();
    });

    test("truncates a self-referencing index at the depth limit instead of expanding forever", async () => {
      requests.length = 0;
      const result = await mapWebsite({ url: indexUrl, maxUrls: 200 });

      const selfUrl = `${indexUrl}/sitemap-self.xml`;
      const selfOutcomes = result.sitemapOutcomes.filter((o) => o.url === selfUrl);
      expect(selfOutcomes.some((o) => o.status === "parse_error")).toBe(true);
      const depthOutcome = selfOutcomes.find((o) => o.status === "parse_error");
      expect(depthOutcome?.error).toMatch(/depth/i);

      // Bounded: the map may be refetched while walking down, but never past the cap.
      expect(selfOutcomes.length).toBeLessThanOrEqual(MAX_SITEMAP_DEPTH + 2);
      expect(requests.filter((p) => p === "/sitemap-self.xml").length).toBeLessThanOrEqual(MAX_SITEMAP_DEPTH + 1);
    });

    test("marks sitemaps left unread when maxUrls is reached as skipped_limit", async () => {
      const result = await mapWebsite({ url: indexUrl, maxUrls: 1 });

      expect(result.urls.length).toBe(1);
      const skipped = result.sitemapOutcomes.filter((o) => o.status === "skipped_limit");
      expect(skipped.length).toBeGreaterThan(0);
      expect(skipped.some((o) => o.url === `${indexUrl}/pages-broken.xml`)).toBe(true);

      const statuses = new Set(result.sitemapOutcomes.map((o) => o.status));
      for (const status of statuses) {
        expect(["ok", "http_error", "parse_error", "skipped_limit"]).toContain(status);
      }
    });

    test("reports an unrecognizable sitemap body as a parse_error", async () => {
      const body = `<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                <sitemap><loc>${indexUrl}/pages-a.xml</loc></sitemap>
              </sitemapindex>`;
      const kind = classifySitemapDocument(body);
      expect(kind).toBe("index");
      expect(classifySitemapDocument("<urlset></urlset>")).toBe("urlset");
      expect(classifySitemapDocument("<html><body>not found</body></html>")).toBe("unknown");
    });
  });

  describe("crawlWebsite", () => {
    test("should recursively crawl pages within depth boundaries and compute token accounting", async () => {
      const result = await crawlWebsite({
        startUrl: `${serverUrl}/page1`,
        maxDepth: 1,
        maxPages: 5,
        mode: "fast",
      });

      expect(result.totalPagesCrawled).toBeGreaterThanOrEqual(1);
      expect(result.totalEstimatedTokens).toBeGreaterThan(0);

      const crawledUrls = result.pages.map((p) => p.url);
      expect(crawledUrls).toContain(`${serverUrl}/page1`);
      expect(crawledUrls).toContain(`${serverUrl}/page1/subpage`);

      const rootPage = result.pages.find((p) => p.url === `${serverUrl}/page1`);
      expect(rootPage?.depth).toBe(0);
      expect(rootPage?.markdown).toContain("Page 1 Content");
    });

    test("should enforce maxPages cutoff even if more links exist", async () => {
      const result = await crawlWebsite({
        startUrl: serverUrl,
        maxDepth: 2,
        maxPages: 2,
        mode: "fast",
      });

      expect(result.pages.length).toBeLessThanOrEqual(2);
    });

    test("should refill a crawl slot as soon as a page finishes instead of waiting for the batch", async () => {
      const result = await crawlWebsite({
        startUrl: `${serverUrl}/pool/hub`,
        maxDepth: 1,
        maxPages: 5,
        concurrency: 2,
        mode: "fast",
      });

      expect(result.pages.length).toBe(5);
      expect(maxInFlight).toBeLessThanOrEqual(2);

      // With fixed waves, /pool/f2 only started after /pool/slow returned.
      const slow = requestLog.find((r) => r.path === "/pool/slow")!;
      const f2 = requestLog.find((r) => r.path === "/pool/f2")!;
      expect(f2.start).toBeLessThan(slow.end!);
    });

    test("should support linkFormat: 'references' during crawling", async () => {
      const result = await crawlWebsite({
        startUrl: `${serverUrl}/page1`,
        maxDepth: 0,
        maxPages: 1,
        mode: "fast",
        linkFormat: "references",
      });

      expect(result.pages.length).toBe(1);
      const markdown = result.pages[0].markdown;
      expect(markdown).toContain("[1]:");
    });
  });
});
