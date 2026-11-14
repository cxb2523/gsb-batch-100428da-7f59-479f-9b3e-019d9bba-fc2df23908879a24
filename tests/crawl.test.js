// Acceptance tests for URL de-duplication and failure reporting.
// Uses only a local HTTP server and stubbed fetch — no external network.
import "./helpers/register.js";
import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";

const { canonicalizeUrl, crawlWebsite } = await import("../dist/index.js");

function startServer(routes) {
  // Match routes regardless of a trailing slash: the crawler fetches each
  // link in its original form (e.g. "/good/"), which must still reach "/good".
  const lookup = (url) => {
    const noQuery = url.split("?")[0];
    if (routes[noQuery] !== undefined) {
      return routes[noQuery];
    }
    const stripped = noQuery.replace(/\/+$/, "");
    return stripped === "" ? routes["/"] : routes[stripped];
  };
  const server = http.createServer((req, res) => {
    const handler = lookup(req.url);
    if (handler === undefined) {
      res.statusCode = 404;
      res.end("not found");
      return;
    }
    handler(req, res);
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({
        base: `http://127.0.0.1:${port}/`,
        close: () => new Promise((r) => server.close(r)),
      });
    });
  });
}

const ok = (body) => (_req, res) => {
  res.statusCode = 200;
  res.setHeader("content-type", "text/html; charset=utf-8");
  res.end(body);
};

const destroy = (req) => req.socket.destroy();

async function collect(startUrl, options) {
  const pages = [];
  const crawler = crawlWebsite(startUrl, { logEnabled: false, ...options });
  let result;
  while (true) {
    const next = await crawler.next();
    if (next.done === true) {
      result = next.value;
      break;
    }
    pages.push(next.value);
  }
  return { pages, result };
}

test("canonicalizeUrl collapses equivalent addresses", () => {
  const base = "http://example.com/docs/";
  const equivalents = [
    "/guide/",
    "/guide",
    "/guide/index.html",
    "/guide/index.htm",
    "/guide?utm_source=x",
    "/guide#section",
    "/guide/index.html?a=1#frag",
  ];
  const keys = equivalents.map((link) => canonicalizeUrl(link, base));
  for (const key of keys) {
    assert.equal(key, "http://example.com/guide");
  }

  // Different documents stay distinct; root path is preserved.
  assert.equal(canonicalizeUrl("/", base), "http://example.com/");
  assert.notEqual(
    canonicalizeUrl("/guide", base),
    canonicalizeUrl("/guide/other", base),
  );

  // Full URLs (GitHub raw entries) canonicalize without a base.
  assert.equal(
    canonicalizeUrl(
      "https://raw.githubusercontent.com/o/r/main/a/index.html",
    ),
    "https://raw.githubusercontent.com/o/r/main/a",
  );
});

test("equivalent links are fetched only once", async () => {
  const fetched = [];
  const track = (handler) => (req, res) => {
    fetched.push(req.url.split("?")[0]);
    handler(req, res);
  };
  const server = await startServer({
    "/": track(
      ok(
        `<html><body><a href="/a/">slash</a>
        <a href="/a/index.html">index</a>
        <a href="/a?x=1">query</a>
        <a href="/a#frag">hash</a></body></html>`,
      ),
    ),
    "/a": track(ok("<html><body>page A</body></html>")),
  });
  try {
    const { pages } = await collect(server.base, { breakOnError: false });
    // "/" plus a single fetch of page A — the four equivalent links collapse.
    assert.deepEqual(fetched, ["/", "/a/"]);
    assert.equal(pages.length, 2);
    const pageA = pages.find((p) => p.path.includes("/a"));
    assert.ok(pageA, "page A is still produced with an accessible path");
  } finally {
    await server.close();
  }
});

test("breakOnError false: other pages are produced and failures are reported", async () => {
  const server = await startServer({
    "/": ok(
      `<html><body>
        <a href="/good/">good</a>
        <a href="/broken/">broken</a>
      </body></html>`,
    ),
    "/good": ok("<html><body>fine page</body></html>"),
    "/broken": destroy,
  });
  try {
    const { pages, result } = await collect(server.base, {
      breakOnError: false,
    });

    // The good page is still yielded; no empty-text Page for the failure.
    const paths = pages.map((p) => p.path);
    assert.ok(paths.some((p) => p.includes("/good")));
    assert.ok(!paths.some((p) => p.includes("/broken")));
    assert.ok(pages.every((p) => p.text !== ""));

    assert.equal(result.failures.length, 1);
    const failure = result.failures[0];
    assert.ok(failure.path.includes("/broken"));
    assert.equal(typeof failure.error, "string");
    assert.ok(failure.error.length > 0);
  } finally {
    await server.close();
  }
});

test("breakOnError true: the first error is thrown", async () => {
  const server = await startServer({
    "/": ok('<html><body><a href="/broken/">x</a></body></html>'),
    "/broken": destroy,
  });
  try {
    await assert.rejects(
      collect(server.base, { breakOnError: true }),
      /socket hang up|fetch failed|ECONN|network|hang up/i,
    );
  } finally {
    await server.close();
  }
});

test("all-success crawl reports no failures", async () => {
  const server = await startServer({
    "/": ok('<html><body><a href="/one/">one</a></body></html>'),
    "/one": ok("<html><body>one</body></html>"),
  });
  try {
    const { pages, result } = await collect(server.base, {
      breakOnError: false,
    });
    assert.equal(pages.length, 2);
    assert.deepEqual(result.failures, []);
  } finally {
    await server.close();
  }
});

test("exclude still filters links before enqueue", async () => {
  const fetched = [];
  const track = (handler) => (req, res) => {
    fetched.push(req.url);
    handler(req, res);
  };
  const server = await startServer({
    "/": track(
      ok(
        `<html><body>
          <a href="/public/">public</a>
          <a href="/private/">private</a>
        </body></html>`,
      ),
    ),
    "/public": track(ok("<html><body>public</body></html>")),
    "/private": ok("<html><body>private</body></html>"),
  });
  try {
    const { pages } = await collect(server.base, {
      breakOnError: false,
      exclude: ["private"],
    });
    assert.deepEqual(fetched, ["/", "/public/"]);
    assert.equal(pages.length, 2);
    assert.ok(!pages.some((p) => p.path.includes("/private")));
  } finally {
    await server.close();
  }
});

test("GitHub repository entry: tree files de-duplicated, excluded and failure-tracked", async () => {
  // Octokit resolves globalThis.fetch; the crawler's raw file downloads go
  // through the node-fetch stub (globalThis.__stubFetch). No real network.
  const requests = [];
  const originalFetch = globalThis.fetch;
  const textResponse = (body, contentType = "text/plain") => ({
    ok: true,
    status: 200,
    headers: new Headers({ "content-type": contentType }),
    text: async () => body,
  });

  globalThis.fetch = async (url) => {
    const target = String(url);
    requests.push(target);
    if (!target.includes("api.github.com")) {
      throw new Error(`unexpected global fetch: ${target}`);
    }
    return textResponse(
      JSON.stringify({
        tree: [
          { type: "tree", path: "docs" },
          { type: "blob", path: "docs/a.md" },
          { type: "blob", path: "docs/b/index.md" },
          { type: "blob", path: "docs/missing.md" },
          { type: "blob", path: "docs/CHANGELOG.md" },
        ],
      }),
      "application/json",
    );
  };

  globalThis.__stubFetch = async (url) => {
    const target = String(url);
    requests.push(target);
    if (target.endsWith("docs/missing.md")) {
      throw new Error("simulated raw fetch failure");
    }
    return textResponse(`markdown of ${target}`);
  };

  try {
    const startUrl = "https://github.com/o/r/tree/main/docs/";
    const { pages, result } = await collect(startUrl, {
      breakOnError: false,
      exclude: ["changelog"],
    });

    const rawTargets = requests.filter((u) =>
      u.includes("raw.githubusercontent.com"),
    );

    // Each blob is requested exactly once; the excluded file never is.
    assert.equal(rawTargets.filter((u) => u.endsWith("docs/a.md")).length, 1);
    assert.equal(
      rawTargets.filter((u) => u.endsWith("docs/b/index.md")).length,
      1,
    );
    assert.equal(
      rawTargets.filter((u) => u.endsWith("docs/missing.md")).length,
      1,
    );
    assert.ok(!rawTargets.some((u) => /CHANGELOG/i.test(u)));

    // Successful markdown is yielded with its full raw address; the failed
    // blob is reported instead of producing an empty-text Page.
    assert.equal(pages.length, 2);
    assert.ok(pages.every((p) => p.text !== ""));
    assert.deepEqual(
      pages.map((p) => p.path).sort(),
      [
        "https://raw.githubusercontent.com/o/r/main/docs/a.md",
        "https://raw.githubusercontent.com/o/r/main/docs/b/index.md",
      ],
    );
    assert.equal(result.failures.length, 1);
    assert.ok(
      result.failures[0].path.endsWith("docs/missing.md"),
      result.failures[0].path,
    );
    assert.match(result.failures[0].error, /simulated raw fetch failure/);
  } finally {
    globalThis.fetch = originalFetch;
    delete globalThis.__stubFetch;
  }
});

test("GitHub repository entry with breakOnError true throws on first failure", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (!String(url).includes("api.github.com")) {
      throw new Error(`unexpected global fetch: ${url}`);
    }
    return {
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      text: async () =>
        JSON.stringify({
          tree: [{ type: "blob", path: "docs/missing.md" }],
        }),
    };
  };
  globalThis.__stubFetch = async () => {
    throw new Error("simulated raw fetch failure");
  };
  try {
    await assert.rejects(
      collect("https://github.com/o/r/tree/main/docs/", {
        breakOnError: true,
      }),
      /simulated raw fetch failure/,
    );
  } finally {
    globalThis.fetch = originalFetch;
    delete globalThis.__stubFetch;
  }
});
