/**
 * Offline test doubles for the Google search scraper.
 *
 * These load synthetic HTML fixtures from ./fixtures into linkedom and run the
 * scraper's real in-page extraction script against them. They stand in for
 * `SpiderPage` so the pagination/merge/outcome logic can be exercised without
 * network access or an API key.
 *
 * The fixtures are hand-written approximations of Google's DOM, NOT captured
 * live responses. Never treat their contents as real search results.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { parseHTML } from "linkedom";

import {
  buildGoogleSerpUrl,
  SERP_EXTRACT_SCRIPT,
  type SerpPage,
} from "../google-search-scraper.js";

export const FIXTURES_DIR = join(dirname(fileURLToPath(import.meta.url)), "fixtures");

export function fixture(name: string): string {
  return readFileSync(join(FIXTURES_DIR, name), "utf8");
}

/**
 * In-memory page: serves fixture HTML per URL and evaluates the real extract
 * script. A route value of `Error` makes `goto()` throw, simulating a
 * navigation failure.
 */
export class StubSerpPage implements SerpPage {
  readonly visited: string[] = [];
  private readonly routes: Map<string, string | Error>;
  private currentHtml = "";
  private currentUrl = "";

  constructor(routes: Map<string, string | Error>) {
    this.routes = routes;
  }

  async goto(url: string): Promise<void> {
    this.visited.push(url);
    const route = this.routes.get(url);
    if (route === undefined) throw new Error(`stub page has no route for ${url}`);
    if (route instanceof Error) throw route;
    this.currentHtml = route;
    this.currentUrl = url;
  }

  async content(): Promise<string> {
    return this.currentHtml;
  }

  async evaluate(expression: string): Promise<unknown> {
    assert.equal(expression, SERP_EXTRACT_SCRIPT, "scraper must use the shared extract script");
    const { document } = parseHTML(this.currentHtml);
    const run = new Function("document", "location", `return ${expression};`);
    return run(document, { href: this.currentUrl });
  }
}

/** Build a URL -> fixture/error route table for `pages` starting at page 1. */
export function routesFor(
  query: string,
  pages: Array<string | Error>
): Map<string, string | Error> {
  const routes = new Map<string, string | Error>();
  pages.forEach((entry, index) => {
    routes.set(buildGoogleSerpUrl(query, index + 1), entry);
  });
  return routes;
}
