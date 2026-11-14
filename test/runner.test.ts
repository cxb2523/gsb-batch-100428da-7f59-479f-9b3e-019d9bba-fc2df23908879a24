import { test } from "node:test";
import assert from "node:assert/strict";
import { register } from "node:module";

// Resolve the extension's `.js` import specifiers to their `.ts` sources.
register("./ts-resolve-loader.mjs", import.meta.url);

const { runCrawl } = await import("../extensions/crawl4ai/runner.ts");

const NODE = process.execPath;

test("runCrawl resolves normally with no interruption", async () => {
	const result = await runCrawl(NODE, ["-e", "console.log('hello')"], 10, undefined, undefined);
	assert.equal(result.exitCode, 0);
	assert.equal(result.interrupted, undefined);
	assert.match(result.stdout, /hello/);
});

test("runCrawl resolves with partial output on timeout", async () => {
	const result = await runCrawl(
		NODE,
		["-e", "console.log('partial-data'); setInterval(() => {}, 1000);"],
		1,
		undefined,
		undefined,
	);
	assert.equal(result.interrupted, "timeout");
	assert.match(result.stdout, /partial-data/);
});

test("runCrawl resolves with partial output on user abort", async () => {
	const controller = new AbortController();
	const resultPromise = runCrawl(
		NODE,
		["-e", "console.log('partial-data'); setInterval(() => {}, 1000);"],
		60,
		controller.signal,
		undefined,
	);
	setTimeout(() => controller.abort(), 200);
	const result = await resultPromise;
	assert.equal(result.interrupted, "aborted");
	assert.match(result.stdout, /partial-data/);
});

test("runCrawl reports an already-aborted signal as aborted", async () => {
	const controller = new AbortController();
	controller.abort();
	const result = await runCrawl(
		NODE,
		["-e", "setInterval(() => {}, 1000);"],
		60,
		controller.signal,
		undefined,
	);
	assert.equal(result.interrupted, "aborted");
	assert.equal(result.stdout, "");
});

test("runCrawl keeps the first reason when abort follows timeout", async () => {
	const controller = new AbortController();
	const resultPromise = runCrawl(
		NODE,
		["-e", "setInterval(() => {}, 1000);"],
		1,
		controller.signal,
		undefined,
	);
	// Abort only after the 1s timeout already fired — timeout must win.
	setTimeout(() => controller.abort(), 1500);
	const result = await resultPromise;
	assert.equal(result.interrupted, "timeout");
});

test("runCrawl keeps the first reason when timeout follows abort", async () => {
	const controller = new AbortController();
	const resultPromise = runCrawl(
		NODE,
		["-e", "setInterval(() => {}, 1000);"],
		60,
		controller.signal,
		undefined,
	);
	setTimeout(() => controller.abort(), 200);
	const result = await resultPromise;
	assert.equal(result.interrupted, "aborted");
});
