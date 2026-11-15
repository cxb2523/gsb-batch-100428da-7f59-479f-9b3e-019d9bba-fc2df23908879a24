import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { runCrawler } from "./runner.js";

type FetchInput = string | URL | Request;

function smartRecruitersSources(identifiers: string[]): string {
  return JSON.stringify({
    provider: "smartrecruiters",
    url_template: "https://example.test/sr/{identifier}/postings",
    jobid_template: "https://example.test/jobs/{identifier}/{job_id}",
    companies: identifiers.map((identifier) => ({ identifier }))
  });
}

function srJob(id: string): Record<string, unknown> {
  return {
    id,
    name: `Job ${id}`,
    location: { city: "Remote" },
    releasedDate: "2026-04-15T00:00:00.000Z"
  };
}

function srPage(ids: string[], totalFound: number, offset: number): Record<string, unknown> {
  return {
    content: ids.map(srJob),
    offset,
    limit: 100,
    totalFound
  };
}

// Routes /postings requests by source identifier and offset, enabling per-page
// success/failure control for the paginating SmartRecruiters crawler.
function routingFetch(routes: Map<string, (offset: number, url: string) => Response>): (input: FetchInput) => Promise<Response> {
  return async (input) => {
    const url = new URL(String(input));
    const match = /\/sr\/([^/]+)\/postings/.exec(url.pathname);
    assert.ok(match, `unexpected url: ${url.toString()}`);
    const identifier = match[1]!;
    const route = routes.get(identifier);
    assert.ok(route, `no mock route for source ${identifier}`);
    const offset = Number(url.searchParams.get("offset") ?? "0");
    return route(offset, url.toString());
  };
}

test("runCrawler writes JSONL and report while isolating failures", async () => {
  const dir = await mkdtemp(join(tmpdir(), "crawler-test-"));
  const sources = join(dir, "sources");
  await mkdir(sources);
  await writeFile(join(sources, "lever.json"), JSON.stringify({
    provider: "lever",
    companies: [{ identifier: "ok" }, { identifier: "missing" }]
  }));

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.includes("/ok?")) {
      return new Response(JSON.stringify([
        { id: "1", text: "Engineer", hostedUrl: "https://jobs.lever.co/ok/1" }
      ]), { status: 200 });
    }
    return new Response("not found", { status: 404 });
  };

  try {
    const out = join(dir, "out", "jobs.jsonl");
    const reportPath = join(dir, "out", "report.json");
    const report = await runCrawler({
      sourcesDir: sources,
      selectedProviders: ["lever"],
      concurrency: 2,
      outFile: out,
      reportFile: reportPath,
      progressEveryMs: 0,
      providerConcurrency: {},
      timeoutMs: 1000,
      retries: 0
    });

    const jsonl = await readFile(out, "utf8");
    assert.match(jsonl, /Engineer/);
    assert.equal(report.providers.lever.succeeded, 1);
    assert.equal(report.providers.lever.failed, 1);
    assert.equal(report.total_jobs, 1);
  } finally {
    globalThis.fetch = originalFetch;
    await rm(dir, { recursive: true, force: true });
  }
});

test("runCrawler skips sources from an exclusion JSONL file", async () => {
  const dir = await mkdtemp(join(tmpdir(), "crawler-test-"));
  const sources = join(dir, "sources");
  await mkdir(sources);
  await writeFile(join(sources, "lever.json"), JSON.stringify({
    provider: "lever",
    companies: [{ identifier: "ok" }, { identifier: "missing" }]
  }));
  const excludeSourcesFile = join(dir, "exclude.jsonl");
  await writeFile(excludeSourcesFile, `${JSON.stringify({
    provider: "lever",
    source_key: "missing",
    reason: "http_404"
  })}\n`);

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.includes("/missing?")) {
      throw new Error("excluded source was fetched");
    }
    return new Response(JSON.stringify([
      { id: "1", text: "Engineer", hostedUrl: "https://jobs.lever.co/ok/1" }
    ]), { status: 200 });
  };

  try {
    const report = await runCrawler({
      sourcesDir: sources,
      selectedProviders: ["lever"],
      concurrency: 2,
      outFile: join(dir, "out", "jobs.jsonl"),
      reportFile: join(dir, "out", "report.json"),
      excludeSourcesFile,
      progressEveryMs: 0,
      providerConcurrency: {},
      timeoutMs: 1000,
      retries: 0
    });

    assert.equal(report.providers.lever.sources, 2);
    assert.equal(report.providers.lever.skipped, 1);
    assert.equal(report.skipped_sources, 1);
    assert.equal(report.skipped_by_provider.lever, 1);
    assert.equal(report.providers.lever.succeeded, 1);
    assert.equal(report.providers.lever.failed, 0);
  } finally {
    globalThis.fetch = originalFetch;
    await rm(dir, { recursive: true, force: true });
  }
});

test("runCrawler filters jobs by updated_at age when maxAgeHours is set", async () => {
  const dir = await mkdtemp(join(tmpdir(), "crawler-test-"));
  const sources = join(dir, "sources");
  await mkdir(sources);
  await writeFile(join(sources, "greenhouse.json"), JSON.stringify({
    provider: "greenhouse",
    companies: [{ identifier: "acme" }]
  }));

  const recent = new Date(Date.now() - (2 * 60 * 60 * 1000)).toISOString();
  const stale = new Date(Date.now() - (48 * 60 * 60 * 1000)).toISOString();

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (!url.includes("boards-api.greenhouse.io")) {
      throw new Error(`unexpected url: ${url}`);
    }
    return new Response(JSON.stringify({
      jobs: [
        { id: "recent", title: "Recent Job", updated_at: recent, absolute_url: "https://example.com/recent" },
        { id: "stale", title: "Stale Job", updated_at: stale, absolute_url: "https://example.com/stale" },
        { id: "missing", title: "Missing Date", absolute_url: "https://example.com/missing" }
      ]
    }), { status: 200 });
  };

  try {
    const out = join(dir, "out", "jobs.jsonl");
    const reportPath = join(dir, "out", "report.json");
    const report = await runCrawler({
      sourcesDir: sources,
      selectedProviders: ["greenhouse"],
      concurrency: 1,
      outFile: out,
      reportFile: reportPath,
      maxAgeHours: 24,
      progressEveryMs: 0,
      providerConcurrency: {},
      timeoutMs: 1000,
      retries: 0
    });

    const jsonl = await readFile(out, "utf8");
    assert.match(jsonl, /Recent Job/);
    assert.doesNotMatch(jsonl, /Stale Job/);
    assert.doesNotMatch(jsonl, /Missing Date/);
    assert.equal(report.total_jobs, 1);
    assert.equal(report.providers.greenhouse.succeeded, 1);
    assert.equal(report.providers.greenhouse.failed, 0);
  } finally {
    globalThis.fetch = originalFetch;
    await rm(dir, { recursive: true, force: true });
  }
});

test("runCrawler passes SmartRecruiters source templates to provider", async () => {
  const dir = await mkdtemp(join(tmpdir(), "crawler-test-"));
  const sources = join(dir, "sources");
  await mkdir(sources);
  await writeFile(join(sources, "smartrecruiters.json"), JSON.stringify({
    provider: "smartrecruiters",
    url_template: "https://example.test/sr/{identifier}/postings",
    jobid_template: "https://example.test/jobs/{identifier}/{job_id}",
    companies: [{ identifier: "acme" }]
  }));

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (!url.includes("https://example.test/sr/acme/postings?")) {
      throw new Error(`unexpected url: ${url}`);
    }
    return new Response(JSON.stringify({
      content: [
        { id: "sr-1", name: "Smart Job", location: { city: "Remote" }, releasedDate: "2026-04-15T00:00:00.000Z" }
      ],
      offset: 0,
      limit: 100,
      totalFound: 1
    }), { status: 200 });
  };

  try {
    const out = join(dir, "out", "jobs.jsonl");
    const reportPath = join(dir, "out", "report.json");
    const report = await runCrawler({
      sourcesDir: sources,
      selectedProviders: ["smartrecruiters"],
      concurrency: 1,
      outFile: out,
      reportFile: reportPath,
      progressEveryMs: 0,
      providerConcurrency: {},
      timeoutMs: 1000,
      retries: 0
    });

    const jsonl = await readFile(out, "utf8");
    assert.match(jsonl, /Smart Job/);
    assert.match(jsonl, /https:\/\/example.test\/jobs\/acme\/sr-1/);
    assert.equal(report.providers.smartrecruiters.succeeded, 1);
    assert.equal(report.total_jobs, 1);
  } finally {
    globalThis.fetch = originalFetch;
    await rm(dir, { recursive: true, force: true });
  }
});

test("runCrawler syncs a persistent catalog across runs", async () => {
  const dir = await mkdtemp(join(tmpdir(), "crawler-test-"));
  const sources = join(dir, "sources");
  const state = join(dir, "state");
  const out = join(dir, "out", "jobs.jsonl");
  const report = join(dir, "out", "report.json");
  const catalogDbFile = join(state, "catalog.sqlite");
  const catalogFile = join(dir, "out", "catalog.jsonl");

  await mkdir(sources);
  await mkdir(state);
  await writeFile(join(sources, "lever.json"), JSON.stringify({
    provider: "lever",
    companies: [{ identifier: "ok" }]
  }));

  let callCount = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (!url.includes("api.lever.co/v0/postings/ok?mode=json")) {
      throw new Error(`unexpected url: ${url}`);
    }

    callCount += 1;
    const jobs = callCount === 1
      ? [
        { id: "1", text: "Job A", hostedUrl: "https://jobs.lever.co/ok/1" },
        { id: "2", text: "Job B", hostedUrl: "https://jobs.lever.co/ok/2" }
      ]
      : [
        { id: "2", text: "Job B Updated", hostedUrl: "https://jobs.lever.co/ok/2" }
      ];

    return new Response(JSON.stringify(jobs), { status: 200 });
  };

  try {
    await runCrawler({
      sourcesDir: sources,
      selectedProviders: ["lever"],
      concurrency: 1,
      outFile: out,
      reportFile: report,
      catalogDbFile,
      catalogFile,
      progressEveryMs: 0,
      providerConcurrency: {},
      timeoutMs: 1000,
      retries: 0
    });

    const firstCatalog = JSON.parse(`[${(await readFile(catalogFile, "utf8")).trim().split(/\r?\n/).join(",")}]`) as Array<Record<string, unknown>>;
    assert.equal(firstCatalog.length, 2);
    const firstJobB = firstCatalog.find((job) => job.job_id === "2");
    assert.ok(firstJobB);

    await runCrawler({
      sourcesDir: sources,
      selectedProviders: ["lever"],
      concurrency: 1,
      outFile: out,
      reportFile: report,
      catalogDbFile,
      catalogFile,
      progressEveryMs: 0,
      providerConcurrency: {},
      timeoutMs: 1000,
      retries: 0
    });

    const secondCatalog = JSON.parse(`[${(await readFile(catalogFile, "utf8")).trim().split(/\r?\n/).join(",")}]`) as Array<Record<string, unknown>>;
    assert.equal(secondCatalog.length, 1);
    assert.equal(secondCatalog[0]?.job_id, "2");
    assert.equal(secondCatalog[0]?.title, "Job B Updated");
    assert.equal(secondCatalog[0]?.first_seen_at, firstJobB.first_seen_at);
    assert.notEqual(secondCatalog[0]?.last_seen_at, firstJobB.last_seen_at);
  } finally {
    globalThis.fetch = originalFetch;
    await rm(dir, { recursive: true, force: true });
  }
});

async function setupPartialCrawlDir(): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), "crawler-partial-"));
  const sources = join(dir, "sources");
  await mkdir(sources);
  await writeFile(join(sources, "smartrecruiters.json"), smartRecruitersSources(["healthy", "flaky", "broken"]));
  return dir;
}

test("runCrawler classifies a paginating source that fails on a later page as partial success", async () => {
  const dir = await setupPartialCrawlDir();
  const sources = join(dir, "sources");
  const routes = new Map<string, (offset: number) => Response>();

  // Healthy source: one page, totalFound equals the page size so no second page.
  routes.set("healthy", () => new Response(JSON.stringify(
    srPage(["h1", "h2"], 2, 0)
  ), { status: 200 }));

  // Flaky source: first page returns 100 jobs of 150, second page errors out.
  routes.set("flaky", (offset) => {
    if (offset === 0) {
      return new Response(JSON.stringify(
        srPage(Array.from({ length: 100 }, (_, index) => `f${index}`), 150, 0)
      ), { status: 200 });
    }
    return new Response("server error", { status: 500 });
  });

  // Broken source: first request already fails, nothing can be emitted.
  routes.set("broken", () => new Response("not found", { status: 404 }));

  const originalFetch = globalThis.fetch;
  globalThis.fetch = routingFetch(routes) as typeof fetch;

  try {
    const out = join(dir, "out", "jobs.jsonl");
    const reportPath = join(dir, "out", "report.json");
    const report = await runCrawler({
      sourcesDir: sources,
      selectedProviders: ["smartrecruiters"],
      concurrency: 3,
      outFile: out,
      reportFile: reportPath,
      progressEveryMs: 0,
      providerConcurrency: {},
      timeoutMs: 1000,
      retries: 0
    });

    const stats = report.providers.smartrecruiters;
    assert.equal(stats.sources, 3);
    assert.equal(stats.succeeded, 1);
    assert.equal(stats.partial, 1);
    assert.equal(stats.failed, 1);
    assert.equal(report.partial_sources, 1);

    // The 100 jobs fetched before the error must be kept in the JSONL output.
    const lines = (await readFile(out, "utf8")).trim().split(/\r?\n/);
    const flakyLines = lines.filter((line) => line.includes('"source_key":"flaky"'));
    assert.equal(flakyLines.length, 100);
    assert.equal(lines.filter((line) => line.includes('"source_key":"healthy"')).length, 2);
    assert.equal(lines.filter((line) => line.includes('"source_key":"broken"')).length, 0);
    assert.equal(stats.jobs, 102);
    assert.equal(report.total_jobs, 102);

    // Every JSONL line is still a valid NormalizedJob with unchanged shape.
    for (const line of lines) {
      const job = JSON.parse(line) as Record<string, unknown>;
      assert.equal(typeof job.provider, "string");
      assert.equal(typeof job.source_key, "string");
      assert.equal(typeof job.job_id, "string");
      assert.equal(typeof job.fetched_at, "string");
    }

    // Failures distinguish the partial source (with jobs) from the fully failed one.
    const partialFailure = report.failures.find((failure) => failure.source_key === "flaky");
    assert.ok(partialFailure);
    assert.equal(partialFailure?.partial, true);
    assert.equal(partialFailure?.jobs_emitted, 100);
    assert.equal(partialFailure?.status, 500);

    const fullFailure = report.failures.find((failure) => failure.source_key === "broken");
    assert.ok(fullFailure);
    assert.equal(fullFailure?.partial, undefined);
    assert.equal(fullFailure?.jobs_emitted, undefined);
    assert.equal(fullFailure?.status, 404);

    // The persisted report file matches the returned report.
    const onDisk = JSON.parse(await readFile(reportPath, "utf8")) as typeof report;
    assert.equal(onDisk.providers.smartrecruiters.partial, 1);
    assert.equal(onDisk.partial_sources, 1);
    assert.equal(onDisk.failures.find((f) => f.source_key === "flaky")?.jobs_emitted, 100);
  } finally {
    globalThis.fetch = originalFetch;
    await rm(dir, { recursive: true, force: true });
  }
});

test("runCrawler counts a source that errors before emitting anything as fully failed", async () => {
  const dir = await setupPartialCrawlDir();
  const sources = join(dir, "sources");
  const routes = new Map<string, () => Response>();
  routes.set("healthy", () => new Response(JSON.stringify(srPage(["h1"], 1, 0)), { status: 200 }));
  routes.set("flaky", () => { throw new Error("unexpected flaky route in this test"); });
  routes.set("broken", () => new Response("boom", { status: 503 }));

  const originalFetch = globalThis.fetch;
  globalThis.fetch = routingFetch(routes) as typeof fetch;

  try {
    const report = await runCrawler({
      sourcesDir: sources,
      selectedProviders: ["smartrecruiters"],
      concurrency: 1,
      outFile: join(dir, "out", "jobs.jsonl"),
      reportFile: join(dir, "out", "report.json"),
      progressEveryMs: 0,
      providerConcurrency: {},
      timeoutMs: 1000,
      retries: 0
    });

    const stats = report.providers.smartrecruiters;
    assert.equal(stats.succeeded, 1);
    assert.equal(stats.partial, 0);
    assert.equal(stats.failed, 2);
    assert.equal(report.partial_sources, 0);

    const failure = report.failures.find((item) => item.source_key === "broken");
    assert.ok(failure);
    assert.equal(failure?.partial, undefined);
    assert.equal(failure?.jobs_emitted, undefined);
  } finally {
    globalThis.fetch = originalFetch;
    await rm(dir, { recursive: true, force: true });
  }
});

test("runCrawler keeps partial jobs in the catalog and does not quarantine partial sources", async () => {
  const dir = await setupPartialCrawlDir();
  const sources = join(dir, "sources");
  const state = join(dir, "state");
  await mkdir(state);
  const catalogDbFile = join(state, "catalog.sqlite");
  const catalogFile = join(dir, "out", "catalog.jsonl");

  const routes = new Map<string, (offset: number) => Response>();
  routes.set("healthy", () => new Response(JSON.stringify(srPage(["h1"], 1, 0)), { status: 200 }));
  routes.set("flaky", (offset) => {
    if (offset === 0) {
      return new Response(JSON.stringify(
        srPage(Array.from({ length: 100 }, (_, index) => `f${index}`), 150, 0)
      ), { status: 200 });
    }
    return new Response("server error", { status: 404 });
  });
  routes.set("broken", () => new Response("not found", { status: 404 }));

  const originalFetch = globalThis.fetch;
  globalThis.fetch = routingFetch(routes) as typeof fetch;

  try {
    await runCrawler({
      sourcesDir: sources,
      selectedProviders: ["smartrecruiters"],
      concurrency: 1,
      outFile: join(dir, "out", "jobs.jsonl"),
      reportFile: join(dir, "out", "report.json"),
      stateDir: state,
      catalogDbFile,
      catalogFile,
      progressEveryMs: 0,
      providerConcurrency: {},
      timeoutMs: 1000,
      retries: 0
    });

    // Catalog finalization must not delete partial-source rows.
    const catalogLines = (await readFile(catalogFile, "utf8")).trim().split(/\r?\n/);
    assert.equal(catalogLines.filter((line) => line.includes('"source_key":"flaky"')).length, 100);
    assert.equal(catalogLines.filter((line) => line.includes('"source_key":"healthy"')).length, 1);

    // Only the fully-failed 404 source is marked dead; the partial one is left rerunnable.
    const deadFiles = await readdir(state);
    const deadFile = deadFiles.find((name) => name.startsWith("dead_slugs_smartrecruiters"));
    assert.ok(deadFile, "expected a dead-slug file for the fully failed 404 source");
    const deadContents = await readFile(join(state, deadFile!), "utf8");
    assert.match(deadContents, /broken/);
    assert.doesNotMatch(deadContents, /flaky/);
  } finally {
    globalThis.fetch = originalFetch;
    await rm(dir, { recursive: true, force: true });
  }
});

test("runCrawler handles partial successes concurrently without losing counts or duplicating jobs", async () => {
  const dir = await mkdtemp(join(tmpdir(), "crawler-concurrent-"));
  const sources = join(dir, "sources");
  await mkdir(sources);
  const flakyCount = 8;
  await writeFile(
    join(sources, "smartrecruiters.json"),
    smartRecruitersSources(Array.from({ length: flakyCount }, (_, index) => `flaky${index}`))
  );

  const routes = new Map<string, (offset: number) => Response>();
  for (let index = 0; index < flakyCount; index += 1) {
    routes.set(`flaky${index}`, (offset) => {
      if (offset === 0) {
        return new Response(JSON.stringify(
          srPage([`j${index}-a`, `j${index}-b`], 5, 0)
        ), { status: 200 });
      }
      return new Response("server error", { status: 500 });
    });
  }

  const originalFetch = globalThis.fetch;
  globalThis.fetch = routingFetch(routes) as typeof fetch;

  try {
    const out = join(dir, "out", "jobs.jsonl");
    const report = await runCrawler({
      sourcesDir: sources,
      selectedProviders: ["smartrecruiters"],
      concurrency: 8,
      outFile: out,
      reportFile: join(dir, "out", "report.json"),
      progressEveryMs: 0,
      providerConcurrency: {},
      timeoutMs: 1000,
      retries: 0
    });

    assert.equal(report.providers.smartrecruiters.partial, flakyCount);
    assert.equal(report.providers.smartrecruiters.failed, 0);
    assert.equal(report.providers.smartrecruiters.succeeded, 0);
    assert.equal(report.partial_sources, flakyCount);
    assert.equal(report.total_jobs, flakyCount * 2);

    const lines = (await readFile(out, "utf8")).trim().split(/\r?\n/);
    assert.equal(lines.length, flakyCount * 2);
    // No duplicate (provider, source, job_id) rows.
    const keys = new Set(lines.map((line) => {
      const job = JSON.parse(line) as { provider: string; source_key: string; job_id: string };
      return `${job.provider}:${job.source_key}:${job.job_id}`;
    }));
    assert.equal(keys.size, lines.length);
  } finally {
    globalThis.fetch = originalFetch;
    await rm(dir, { recursive: true, force: true });
  }
});

test("runCrawler does not duplicate streamed jobs when a source is rerun in a later run", async () => {
  const dir = await setupPartialCrawlDir();
  const sources = join(dir, "sources");
  const routes = new Map<string, (offset: number) => Response>();

  // First run: partial success on page 2. Second run: the same source completes
  // and re-emits page 1 with identical ids before page 2 succeeds.
  let run = 0;
  routes.set("healthy", () => new Response(JSON.stringify(srPage(["h1"], 1, 0)), { status: 200 }));
  routes.set("flaky", (offset) => {
    if (offset === 0) {
      return new Response(JSON.stringify(
        srPage(["shared-1", "shared-2"], 4, 0)
      ), { status: 200 });
    }
    if (run === 0) {
      return new Response("server error", { status: 500 });
    }
    return new Response(JSON.stringify(
      srPage(["new-3", "new-4"], 4, 100)
    ), { status: 200 });
  });
  routes.set("broken", () => new Response("not found", { status: 404 }));

  const originalFetch = globalThis.fetch;
  globalThis.fetch = routingFetch(routes) as typeof fetch;

  try {
    const out = join(dir, "out", "jobs.jsonl");
    const options = {
      sourcesDir: sources,
      selectedProviders: ["smartrecruiters"] as Array<"smartrecruiters">,
      concurrency: 1,
      outFile: out,
      reportFile: join(dir, "out", "report.json"),
      progressEveryMs: 0,
      providerConcurrency: {},
      timeoutMs: 1000,
      retries: 0
    };

    const firstReport = await runCrawler(options);
    assert.equal(firstReport.providers.smartrecruiters.partial, 1);
    const firstLines = (await readFile(out, "utf8")).trim().split(/\r?\n/);
    assert.equal(firstLines.filter((line) => line.includes('"source_key":"flaky"')).length, 2);

    run = 1;
    const secondReport = await runCrawler(options);
    assert.equal(secondReport.providers.smartrecruiters.partial, 0);
    assert.equal(secondReport.providers.smartrecruiters.succeeded, 2);

    // The second run writes to a fresh stream over the same path; within that run
    // the shared page-1 ids must appear exactly once, not once per page emission.
    const secondLines = (await readFile(out, "utf8")).trim().split(/\r?\n/);
    const flakyIds = secondLines
      .filter((line) => line.includes('"source_key":"flaky"'))
      .map((line) => (JSON.parse(line) as { job_id: string }).job_id)
      .sort();
    assert.deepEqual(flakyIds, ["new-3", "new-4", "shared-1", "shared-2"]);
  } finally {
    globalThis.fetch = originalFetch;
    await rm(dir, { recursive: true, force: true });
  }
});

test("runCrawler marks a Workday source partial when a later POST page fails", async () => {
  const dir = await mkdtemp(join(tmpdir(), "crawler-workday-"));
  const sources = join(dir, "sources");
  await mkdir(sources);
  await writeFile(join(sources, "workday.json"), JSON.stringify({
    provider: "workday",
    companies: [{ tenant: "acme", shard: "wd5", site: "careers" }]
  }));

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    assert.match(url, /acme\.wd5\.myworkdayjobs\.com/);
    const body = JSON.parse(String((init as RequestInit).body)) as { offset: number };
    if (body.offset === 0) {
      return new Response(JSON.stringify({
        total: 40,
        jobPostings: Array.from({ length: 20 }, (_, index) => ({
          jobId: `R${index}`,
          title: `Analyst ${index}`,
          externalPath: `/job/TORONTO-CAN/Analyst-R${index}`,
          locationsText: "Toronto"
        }))
      }), { status: 200 });
    }
    return new Response("server error", { status: 500 });
  };

  try {
    const out = join(dir, "out", "jobs.jsonl");
    const report = await runCrawler({
      sourcesDir: sources,
      selectedProviders: ["workday"],
      concurrency: 1,
      outFile: out,
      reportFile: join(dir, "out", "report.json"),
      progressEveryMs: 0,
      providerConcurrency: {},
      timeoutMs: 1000,
      retries: 0
    });

    assert.equal(report.providers.workday.partial, 1);
    assert.equal(report.providers.workday.failed, 0);
    assert.equal(report.providers.workday.succeeded, 0);
    assert.equal(report.total_jobs, 20);

    const failure = report.failures[0];
    assert.ok(failure);
    assert.equal(failure?.partial, true);
    assert.equal(failure?.jobs_emitted, 20);
    assert.equal(failure?.source_key, "acme/wd5/careers");

    const lines = (await readFile(out, "utf8")).trim().split(/\r?\n/);
    assert.equal(lines.length, 20);
    for (const line of lines) {
      const job = JSON.parse(line) as { provider: string; source_key: string };
      assert.equal(job.provider, "workday");
      assert.equal(job.source_key, "acme/wd5/careers");
    }
  } finally {
    globalThis.fetch = originalFetch;
    await rm(dir, { recursive: true, force: true });
  }
});
