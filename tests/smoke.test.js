// Basic smoke test for the built module surface.
import test from "node:test";
import assert from "node:assert/strict";

import { canonicalizeUrl, crawlWebsite } from "../dist/index.js";

test("module exports the crawler and canonicalization helper", () => {
  assert.equal(typeof crawlWebsite, "function");
  assert.equal(typeof canonicalizeUrl, "function");
});

test("canonicalizeUrl keeps the root path stable", () => {
  assert.equal(
    canonicalizeUrl("/", "http://example.com/docs/"),
    "http://example.com/",
  );
});
