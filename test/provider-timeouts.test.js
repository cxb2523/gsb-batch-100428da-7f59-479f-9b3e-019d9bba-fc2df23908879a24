import assert from "node:assert/strict";
import test from "node:test";

import {
  fetchImdbPosterCandidates,
  findPostersSequential,
  runProviderWithDeadline,
} from "../lib/providers.js";

const poster = (name) => `https://images.example.com/${name}.jpg`;
const stalled = () => new Promise(() => {});

test("a stalled IMDb provider times out and a later provider succeeds", async () => {
  const result = await findPostersSequential("Movie", null, {
    providers: [
      { name: "imdb", fetcher: stalled },
      { name: "itunes", fetcher: async () => [poster("later")] },
    ],
    providerTimeoutMs: 10,
    overallTimeoutMs: 50,
  });

  assert.deepEqual(result, {
    posters: [poster("later")],
    source: "itunes",
    sourcesTried: ["itunes"],
    sourceOutcomes: [
      { name: "imdb", status: "failed", reason: "timeout" },
      { name: "itunes", status: "contributed" },
    ],
  });
});

test("a later timeout preserves posters already collected", async () => {
  const result = await findPostersSequential("Movie", null, {
    providers: [
      { name: "imdb", fetcher: async () => [] },
      { name: "itunes", fetcher: async () => [poster("collected")] },
      { name: "wikipedia", fetcher: stalled },
    ],
    providerTimeoutMs: 10,
    overallTimeoutMs: 60,
  });

  assert.deepEqual(result.posters, [poster("collected")]);
  assert.equal(result.source, "itunes");
  assert.deepEqual(result.sourcesTried, ["itunes", "wikipedia"]);
  assert.deepEqual(result.sourceOutcomes, [
    { name: "imdb", status: "no_matches" },
    { name: "itunes", status: "contributed" },
    { name: "wikipedia", status: "failed", reason: "timeout" },
  ]);
});

test("a timed-out middle source does not stop later sources from contributing", async () => {
  const result = await findPostersSequential("Movie", null, {
    providers: [
      { name: "imdb", fetcher: async () => [] },
      { name: "tmdb", fetcher: stalled },
      { name: "itunes", fetcher: async () => [poster("after-timeout")] },
    ],
    providerTimeoutMs: 10,
    overallTimeoutMs: 120,
  });

  assert.deepEqual(result.posters, [poster("after-timeout")]);
  assert.equal(result.source, "itunes");
  assert.deepEqual(result.sourcesTried, ["tmdb", "itunes"]);
  assert.deepEqual(result.sourceOutcomes, [
    { name: "imdb", status: "no_matches" },
    { name: "tmdb", status: "failed", reason: "timeout" },
    { name: "itunes", status: "contributed" },
  ]);
});

test("an errored source is reported as failed and later sources still run", async () => {
  const result = await findPostersSequential("Movie", null, {
    providers: [
      { name: "imdb", fetcher: async () => [] },
      { name: "itunes", fetcher: async () => { throw new Error("iTunes unavailable"); } },
      { name: "wikipedia", fetcher: async () => [poster("wiki")] },
    ],
    providerTimeoutMs: 50,
    overallTimeoutMs: 200,
  });

  assert.deepEqual(result.posters, [poster("wiki")]);
  assert.deepEqual(result.sourceOutcomes, [
    { name: "imdb", status: "no_matches" },
    { name: "itunes", status: "failed", reason: "error" },
    { name: "wikipedia", status: "contributed" },
  ]);
});

test("a source whose posters do not match the movie is reported as no_matches", async () => {
  const result = await findPostersSequential("Movie", 2001, {
    providers: [
      { name: "imdb", fetcher: async () => [] },
      { name: "itunes", fetcher: async () => [{ url: poster("wrong-year"), title: "Movie", year: 1999 }] },
    ],
    providerTimeoutMs: 50,
    overallTimeoutMs: 200,
  });

  assert.deepEqual(result.posters, []);
  assert.deepEqual(result.sourceOutcomes, [
    { name: "imdb", status: "no_matches" },
    { name: "itunes", status: "no_matches" },
  ]);
});

test("budget exhaustion between sources keeps collected posters and omits unstarted sources", async () => {
  let clock = 0;
  const started = [];
  const result = await findPostersSequential("Movie", null, {
    providers: [
      { name: "imdb", fetcher: async () => { started.push("imdb"); clock = 60; return []; } },
      { name: "itunes", fetcher: async () => { started.push("itunes"); clock = 110; return [poster("collected-before-deadline")]; } },
      { name: "wikipedia", fetcher: async () => { started.push("wikipedia"); return [poster("too-late")]; } },
      { name: "firecrawl", fetcher: async () => { started.push("firecrawl"); return [poster("also-too-late")]; } },
    ],
    providerTimeoutMs: 50,
    overallTimeoutMs: 100,
    now: () => clock,
  });

  assert.deepEqual(result.posters, [poster("collected-before-deadline")]);
  assert.equal(result.source, "itunes");
  assert.deepEqual(result.sourcesTried, ["itunes"]);
  assert.deepEqual(result.sourceOutcomes, [
    { name: "imdb", status: "no_matches" },
    { name: "itunes", status: "contributed" },
  ]);
  assert.deepEqual(started, ["imdb", "itunes"]);
});

test("all failed sources produce empty posters and report each started source", async () => {
  let clock = 0;
  const started = [];
  const stalling = ["imdb", "tmdb", "itunes"].map((name) => ({
    name,
    fetcher: () => {
      started.push(name);
      clock += 12;
      return stalled();
    },
  }));
  const result = await findPostersSequential("Movie", null, {
    providers: [
      ...stalling,
      { name: "wikipedia", fetcher: async () => { started.push("wikipedia"); return []; } },
    ],
    providerTimeoutMs: 10,
    overallTimeoutMs: 30,
    now: () => clock,
  });

  assert.deepEqual(result.posters, []);
  assert.deepEqual(started, ["imdb", "tmdb", "itunes"]);
  assert.deepEqual(result.sourceOutcomes, [
    { name: "imdb", status: "failed", reason: "timeout" },
    { name: "tmdb", status: "failed", reason: "timeout" },
    { name: "itunes", status: "failed", reason: "timeout" },
  ]);
});

test("all stalled providers produce a bounded response and stop at the overall deadline", async () => {
  const started = [];
  const providers = ["imdb", "one", "two", "not-started"].map((name) => ({
    name,
    fetcher: () => {
      started.push(name);
      return stalled();
    },
  }));
  const beganAt = Date.now();
  const result = await findPostersSequential("Movie", null, {
    providers,
    providerTimeoutMs: 20,
    overallTimeoutMs: 35,
  });

  assert.ok(Date.now() - beganAt < 150, "search exceeded its bounded deadline");
  assert.deepEqual(result.posters, []);
  assert.equal(started.includes("not-started"), false);
});

test("a stalled response body is covered and its fetch signal is aborted", async (t) => {
  const originalFetch = globalThis.fetch;
  let receivedSignal;
  let aborted = false;
  globalThis.fetch = async (_url, { signal } = {}) => {
    receivedSignal = signal;
    signal.addEventListener("abort", () => { aborted = true; }, { once: true });
    return { ok: true, json: stalled };
  };
  t.after(() => { globalThis.fetch = originalFetch; });

  const result = await findPostersSequential("Movie", null, {
    providers: [
      {
        name: "imdb",
        fetcher: (context) => fetchImdbPosterCandidates("Movie", null, context),
      },
      { name: "itunes", fetcher: async () => [poster("fallback")] },
    ],
    providerTimeoutMs: 10,
    overallTimeoutMs: 50,
  });

  assert.ok(receivedSignal instanceof AbortSignal);
  assert.equal(aborted, true);
  assert.deepEqual(result.posters, [poster("fallback")]);
});

test("fast successful searches retain IMDb priority and response fields", async () => {
  const result = await findPostersSequential("Movie", null, {
    providers: [
      { name: "tmdb", fetcher: async () => [poster("tmdb")] },
      { name: "imdb", fetcher: async () => [poster("imdb")] },
      { name: "itunes", fetcher: async () => [poster("itunes")] },
    ],
    providerTimeoutMs: 50,
    overallTimeoutMs: 200,
  });

  assert.equal(result.posters[0], poster("imdb"));
  assert.deepEqual(new Set(result.posters), new Set([
    poster("imdb"), poster("tmdb"), poster("itunes"),
  ]));
  assert.equal(result.source, "imdb");
  assert.deepEqual(result.sourcesTried, ["tmdb", "itunes"]);
});

test("deadline helper clears its timer after success and timeout", async () => {
  const activeTimers = new Set();
  const setTimer = (callback, delay) => {
    const timer = setTimeout(callback, delay);
    activeTimers.add(timer);
    return timer;
  };
  const clearTimer = (timer) => {
    activeTimers.delete(timer);
    clearTimeout(timer);
  };

  assert.deepEqual(await runProviderWithDeadline(async () => ["ok"], {
    timeoutMs: 20,
    setTimer,
    clearTimer,
  }), ["ok"]);
  assert.equal(activeTimers.size, 0);

  await assert.rejects(runProviderWithDeadline(stalled, {
    timeoutMs: 5,
    setTimer,
    clearTimer,
  }), /timed out/);
  assert.equal(activeTimers.size, 0);
});
