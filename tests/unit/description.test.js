import { test } from "node:test";
import assert from "node:assert/strict";

import {
  DESCRIPTION_FAILURE_STRATEGY,
  DESCRIPTION_STATUS,
  fetchDescription,
} from "../../src/description.js";

const makePage = ({ gotoImpl, contentHtml }) => ({
  goto: gotoImpl || (async () => undefined),
  content: async () => contentHtml ?? "<html><body><p>hello</p></body></html>",
});

test("fetchDescription", async (t) => {
  await t.test("success: returns body HTML and success status", async () => {
    const visitedUrls = [];
    const page = makePage({
      gotoImpl: async (url) => {
        visitedUrls.push(url);
      },
      contentHtml:
        '<html><body><div class="desc"><img src="a.jpg"></div></body></html>',
    });

    const result = await fetchDescription({
      page,
      descriptionUrl: "https://desc.example.com/d/1",
      strategy: DESCRIPTION_FAILURE_STRATEGY.CONTINUE,
      productId: "100500",
    });

    assert.deepStrictEqual(visitedUrls, ["https://desc.example.com/d/1"]);
    assert.equal(
      result.descriptionData,
      '<div class="desc"><img src="a.jpg"></div>'
    );
    assert.deepStrictEqual(result.descriptionStatus, {
      status: DESCRIPTION_STATUS.SUCCESS,
      reason: null,
    });
  });

  await t.test("failed: CONTINUE degrades to null with a readable reason", async () => {
    const timeoutError = new Error("Navigation timeout of 30000 ms exceeded");
    const page = makePage({
      gotoImpl: async () => {
        throw timeoutError;
      },
    });

    const result = await fetchDescription({
      page,
      descriptionUrl: "https://desc.example.com/d/2",
      strategy: DESCRIPTION_FAILURE_STRATEGY.CONTINUE,
      productId: "100501",
    });

    assert.equal(result.descriptionData, null);
    assert.equal(result.descriptionStatus.status, DESCRIPTION_STATUS.FAILED);
    assert.match(
      result.descriptionStatus.reason,
      /Description page fetch failed for https:\/\/desc\.example\.com\/d\/2/
    );
    assert.match(result.descriptionStatus.reason, /Navigation timeout/);
  });

  await t.test("failed: FAIL (default) rethrows the original error", async () => {
    const timeoutError = new Error("Navigation timeout of 30000 ms exceeded");
    const page = makePage({
      gotoImpl: async () => {
        throw timeoutError;
      },
    });

    await assert.rejects(
      fetchDescription({
        page,
        descriptionUrl: "https://desc.example.com/d/3",
        strategy: DESCRIPTION_FAILURE_STRATEGY.FAIL,
      }),
      /Navigation timeout/
    );

    // Default strategy is FAIL when none is provided
    await assert.rejects(
      fetchDescription({
        page,
        descriptionUrl: "https://desc.example.com/d/3",
      }),
      /Navigation timeout/
    );
  });

  await t.test("failed: parsing error after navigation is also degraded", async () => {
    const page = {
      goto: async () => undefined,
      content: async () => {
        throw new Error("Target closed");
      },
    };

    const result = await fetchDescription({
      page,
      descriptionUrl: "https://desc.example.com/d/4",
      strategy: DESCRIPTION_FAILURE_STRATEGY.CONTINUE,
    });

    assert.equal(result.descriptionData, null);
    assert.equal(result.descriptionStatus.status, DESCRIPTION_STATUS.FAILED);
    assert.match(result.descriptionStatus.reason, /Target closed/);
  });

  await t.test("skipped: no description URL returns null and skipped status", async () => {
    const gotoCalls = [];
    const page = makePage({
      gotoImpl: async (url) => {
        gotoCalls.push(url);
      },
    });

    const result = await fetchDescription({
      page,
      descriptionUrl: null,
      strategy: DESCRIPTION_FAILURE_STRATEGY.CONTINUE,
      productId: "100502",
    });

    assert.deepStrictEqual(gotoCalls, []);
    assert.equal(result.descriptionData, null);
    assert.deepStrictEqual(result.descriptionStatus, {
      status: DESCRIPTION_STATUS.SKIPPED,
      reason:
        "No description URL found for product 100502; description fetch skipped",
    });
  });

  await t.test("skipped: reason stays readable without a product id", async () => {
    const page = makePage({});

    const result = await fetchDescription({
      page,
      descriptionUrl: undefined,
      strategy: DESCRIPTION_FAILURE_STRATEGY.CONTINUE,
    });

    assert.equal(result.descriptionData, null);
    assert.equal(result.descriptionStatus.status, DESCRIPTION_STATUS.SKIPPED);
    assert.match(result.descriptionStatus.reason, /No description URL found/);
  });

  await t.test("skipped: never rejects even under the FAIL strategy", async () => {
    const page = makePage({});

    const result = await fetchDescription({
      page,
      descriptionUrl: null,
      strategy: DESCRIPTION_FAILURE_STRATEGY.FAIL,
      productId: "100503",
    });

    assert.equal(result.descriptionData, null);
    assert.equal(result.descriptionStatus.status, DESCRIPTION_STATUS.SKIPPED);
  });
});
