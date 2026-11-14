// End-to-end check of the bin's stderr failure report. Spawns a tiny static
// server, then runs the built dist/bin.js against it. Not run by node --test;
// invoked manually via: node tests/e2e/bin-report.mjs
import { spawn } from "node:child_process";
import http from "node:http";
import { mkdtempSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

const pages = {
  "/": '<html><body><a href="/ok/">ok</a><a href="/boom/">boom</a></body></html>',
  "/ok": "<html><body>fine content</body></html>",
  "/boom": null, // socket destroyed -> fetch error
};

const server = http.createServer((req, res) => {
  const key = req.url.split("?")[0].replace(/\/+$/, "") || "/";
  const body = pages[key];
  if (body === null) {
    req.socket.destroy();
    return;
  }
  if (body === undefined) {
    res.statusCode = 404;
    res.end("nf");
    return;
  }
  res.setHeader("content-type", "text/html");
  res.end(body);
});

await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}/`;
const work = mkdtempSync(path.join(tmpdir(), "crawlrag-e2e-"));

function runBin(args) {
  return new Promise((resolve) => {
    const child = spawn(
      process.execPath,
      [path.join(repoRoot, "dist/bin.js"), ...args],
      { cwd: repoRoot },
    );
    let out = "";
    let err = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (err += d));
    child.on("close", (code) => resolve({ code, out, err }));
  });
}

let failures = 0;
const check = (cond, label) => {
  console.log(`${cond ? "PASS" : "FAIL"}  ${label}`);
  if (!cond) failures++;
};

// 1) JSON output with --no-break-on-error and --no-log
const jsonPath = path.join(work, "out.json");
const jsonRun = await runBin([
  base,
  jsonPath,
  "--no-break-on-error",
  "--no-log",
]);
check(jsonRun.code === 0, "json run exits 0");
check(
  jsonRun.err.includes("1 failed page") && jsonRun.err.includes("/boom"),
  "stderr lists failure count and the failed address with --no-log",
);
check(!jsonRun.err.includes("🚀"), "no crawl logs on stderr with --no-log");
const json = JSON.parse(readFileSync(jsonPath, "utf-8"));
check(json.length === 2, "json contains the two successful pages");
check(
  json.every((p) => p.text && p.text.length),
  "no empty-text page was written",
);
check(!json.some((p) => p.path.includes("/boom")), "failed page absent from json");

// 2) Markdown directory output
const mdDir = path.join(work, "md");
const mdRun = await runBin([base, mdDir + path.sep, "--no-break-on-error"]);
check(mdRun.code === 0, "markdown run exits 0");
check(
  mdRun.err.includes("1 failed page") && mdRun.err.includes("/boom"),
  "markdown run still reports failures on stderr",
);
check(existsSync(path.join(mdDir, "ok.md")), "markdown file written for /ok");
check(!existsSync(path.join(mdDir, "boom.md")), "no markdown file for failure");

// 3) All-success run prints no failure list
const goodServerBase = base; // reuse: crawl only the ok page via exclude
const cleanPath = path.join(work, "clean.json");
const cleanRun = await runBin([
  goodServerBase,
  cleanPath,
  "--no-break-on-error",
  "-e",
  "boom",
]);
check(cleanRun.code === 0, "clean run exits 0");
check(
  !cleanRun.err.includes("failed page"),
  "no failure report when every page succeeds",
);

// 4) Default breakOnError true: first error propagates, non-zero exit
const breakRun = await runBin([base, path.join(work, "x.json")]);
check(
  breakRun.code === 1 && /hang up|fetch failed|ECONN|network/i.test(breakRun.err),
  "breakOnError true surfaces the error and exits non-zero",
);

server.close();
console.log(failures === 0 ? "\nALL E2E CHECKS PASSED" : `\n${failures} CHECK(S) FAILED`);
process.exit(failures === 0 ? 0 : 1);
