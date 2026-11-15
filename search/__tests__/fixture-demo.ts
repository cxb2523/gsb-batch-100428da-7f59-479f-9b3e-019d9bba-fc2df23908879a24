/**
 * Offline demo for the Google search scraper's pagination + outcome logic.
 *
 * Drives `runCli` with synthetic fixtures instead of a live browser, so the
 * normal path and the blocked path can be reproduced locally with no API key
 * and no requests to Google.
 *
 * Usage:
 *   npx tsx search/__tests__/fixture-demo.ts normal
 *   npx tsx search/__tests__/fixture-demo.ts blocked
 *   npx tsx search/__tests__/fixture-demo.ts mixed
 *
 * Exit code mirrors the scraper: 0 usable, 1 all blocked/errored.
 * Output is synthetic fixture data — not live Google results.
 */

import { runCli } from "../google-search-scraper.js";
import { fixture, routesFor, StubSerpPage } from "./fixture-page.js";

const SCENARIOS: Record<string, { pages: Array<string | Error>; pageCount: number }> = {
  normal: { pages: [fixture("serp-page1.html"), fixture("serp-page2.html")], pageCount: 2 },
  blocked: { pages: [fixture("serp-captcha.html"), fixture("serp-consent.html")], pageCount: 2 },
  mixed: {
    pages: [fixture("serp-captcha.html"), new Error("navigation timed out"), fixture("serp-page1.html")],
    pageCount: 3,
  },
};

async function main(): Promise<number> {
  const scenarioName = process.argv[2] ?? "normal";
  const scenario = SCENARIOS[scenarioName];
  if (!scenario) {
    process.stderr.write(`unknown scenario "${scenarioName}" (use normal, blocked, or mixed)\n`);
    return 2;
  }

  const query = "fixture query";
  const page = new StubSerpPage(routesFor(query, scenario.pages));

  return runCli(["--query", query, "--pages", String(scenario.pageCount)], {
    sleep: async () => undefined,
    openBrowser: async () => ({ page, close: async () => undefined }),
  });
}

process.exitCode = await main();
