import { test } from "node:test";
import assert from "node:assert/strict";

// Minimal product "data" produced after the main page is scraped. The shape
// mirrors what extractDataFromApiResponse returns for the pieces the scraper
// and transform layer consume.
const buildProductData = ({ withDescriptionUrl = true } = {}) => ({
  productInfoComponent: {
    subject: "Test Widget",
    categoryId: 42,
    id: 123456,
  },
  inventoryComponent: { totalQuantity: 10, totalAvailQuantity: 8 },
  tradeComponent: { formatTradeCount: "100+" },
  sellerComponent: {},
  storeFeedbackComponent: {},
  feedbackComponent: { totalValidNum: 1 },
  imageComponent: { imagePathList: [] },
  skuComponent: { productSKUPropertyList: [] },
  priceComponent: { skuPriceList: [] },
  productPropComponent: { props: [] },
  currencyComponent: { currencyCode: "USD" },
  webGeneralFreightCalculateComponent: { originalLayoutResultList: [] },
  productDescComponent: {
    descriptionUrl: withDescriptionUrl
      ? "https://desc.example.com/p/123456"
      : null,
  },
});

const reviewFixture = {
  anonymous: false,
  buyerName: "John",
  buyerGender: "M",
  buyerCountry: "US",
  buyerEval: 100,
  skuInfo: "Color: Red",
  evalDate: "2024-01-01",
  buyerFeedback: "Great product",
  images: [],
  thumbnails: [],
};

test("AliexpressProductScraper description failure strategy", async (t) => {
  // Mutable harness so individual subtests can reconfigure the fake browser
  // behavior while sharing the module mocks registered once for this file.
  const harness = {
    productData: buildProductData(),
    descriptionGotoError: null,
    descriptionHtml: "<html><body><p>desc html</p></body></html>",
    mainGotoError: null,
    browserClosed: false,
    reviewRequestUrls: [],
    page: null,
  };

  const makePage = () => {
    const listeners = {};
    const page = {
      on: (event, handler) => {
        listeners[event] = handler;
      },
      goto: async (url) => {
        if (url.includes("/item/")) {
          if (harness.mainGotoError) {
            throw harness.mainGotoError;
          }
          return;
        }
        // Any other navigation is the description page
        if (harness.descriptionGotoError) {
          throw harness.descriptionGotoError;
        }
      },
      content: async () => harness.descriptionHtml,
      // Emulate the legacy window.runParams branch so the scraper's polling
      // loop picks up the prepared product data on its first iteration.
      evaluate: async () => harness.productData,
      listeners,
    };
    harness.page = page;
    return page;
  };

  const fakeBrowser = {
    newPage: async () => makePage(),
    close: async () => {
      harness.browserClosed = true;
    },
  };

  const fakePuppeteer = {
    use: () => undefined,
    launch: async () => fakeBrowser,
    default: undefined,
  };
  fakePuppeteer.default = fakePuppeteer;

  t.mock.module("puppeteer-extra", {
    exports: { default: fakePuppeteer },
  });

  t.mock.module("node-fetch", {
    exports: {
      default: async (url) => {
        harness.reviewRequestUrls.push(url);
        return {
          ok: true,
          json: async () => ({ data: { evaViewList: [reviewFixture] } }),
        };
      },
    },
  });

  const { default: scrape } = await import(
    "../../src/aliexpressProductScraper.js"
  );
  const { DESCRIPTION_FAILURE_STRATEGY, DESCRIPTION_STATUS } = await import(
    "../../src/description.js"
  );

  t.afterEach(() => {
    harness.productData = buildProductData();
    harness.descriptionGotoError = null;
    harness.mainGotoError = null;
    harness.descriptionHtml = "<html><body><p>desc html</p></body></html>";
    harness.browserClosed = false;
    harness.reviewRequestUrls = [];
  });

  await t.test("continue strategy: description success keeps full data and reports success", async () => {
    const result = await scrape("123456", {
      descriptionFailureStrategy: DESCRIPTION_FAILURE_STRATEGY.CONTINUE,
    });

    assert.equal(result.title, "Test Widget");
    assert.equal(result.description, "<p>desc html</p>");
    assert.equal(result.descriptionStatus.status, DESCRIPTION_STATUS.SUCCESS);
    assert.equal(result.descriptionStatus.reason, null);
    // Reviews are collected independently of the description step
    assert.equal(result.reviews.length, 1);
    assert.equal(result.reviews[0].name, "John");
    assert.equal(harness.browserClosed, true);
  });

  await t.test("continue strategy: description page failure is degraded, not thrown", async () => {
    harness.descriptionGotoError = new Error(
      "Navigation timeout of 60000 ms exceeded"
    );

    const result = await scrape("123456", {
      descriptionFailureStrategy: DESCRIPTION_FAILURE_STRATEGY.CONTINUE,
    });

    // Main body data survives the description failure
    assert.equal(result.title, "Test Widget");
    assert.equal(result.description, null);
    assert.equal(result.descriptionStatus.status, DESCRIPTION_STATUS.FAILED);
    assert.match(
      result.descriptionStatus.reason,
      /Description page fetch failed/
    );
    assert.match(result.descriptionStatus.reason, /Navigation timeout/);
    // Review collection and the transform step are unaffected
    assert.equal(result.reviews.length, 1);
    assert.equal(harness.browserClosed, true);
  });

  await t.test("continue strategy: missing description link is reported as skipped", async () => {
    // Data without a description URL; the fake page's evaluate() returns it
    // via the legacy runParams branch on the first polling iteration.
    harness.productData = buildProductData({ withDescriptionUrl: false });

    const result = await scrape("123456", {
      descriptionFailureStrategy: DESCRIPTION_FAILURE_STRATEGY.CONTINUE,
    });

    assert.equal(result.description, null);
    assert.equal(result.descriptionStatus.status, DESCRIPTION_STATUS.SKIPPED);
    assert.match(
      result.descriptionStatus.reason,
      /No description URL found for product 123456/
    );
    assert.equal(result.reviews.length, 1);
    assert.equal(harness.browserClosed, true);
  });

  await t.test("default strategy: description failure still rejects the whole scrape", async () => {
    harness.descriptionGotoError = new Error(
      "Navigation timeout of 60000 ms exceeded"
    );

    await assert.rejects(
      scrape("123456"),
      /Navigation timeout/
    );
    assert.equal(harness.browserClosed, true);
  });

  await t.test("default strategy: output shape has no descriptionStatus field", async () => {
    const result = await scrape("123456");

    assert.equal(result.description, "<p>desc html</p>");
    assert.equal("descriptionStatus" in result, false);
    assert.equal(result.reviews.length, 1);
  });

  await t.test("continue strategy does not swallow main product page failures", async () => {
    harness.mainGotoError = new Error("net::ERR_NAME_NOT_RESOLVED");

    await assert.rejects(
      scrape("123456", {
        descriptionFailureStrategy: DESCRIPTION_FAILURE_STRATEGY.CONTINUE,
      }),
      /net::ERR_NAME_NOT_RESOLVED/
    );
    assert.equal(harness.browserClosed, true);
  });

  await t.test("invalid strategy value is rejected before launching the browser", async () => {
    await assert.rejects(
      scrape("123456", { descriptionFailureStrategy: "bogus" }),
      /Invalid descriptionFailureStrategy/
    );
    // Browser launch happens after validation, so nothing was opened/closed
    assert.equal(harness.browserClosed, false);
  });

  await t.test("reviewsCount and filterReviewsBy keep their semantics", async () => {
    await scrape("123456", {
      reviewsCount: 20,
      filterReviewsBy: 5,
      descriptionFailureStrategy: DESCRIPTION_FAILURE_STRATEGY.CONTINUE,
    });

    const requestedUrls = harness.reviewRequestUrls;
    assert.equal(requestedUrls.length, 1);
    assert.match(requestedUrls[0], /productId=123456/);
    assert.match(requestedUrls[0], /page=1/);
    assert.match(requestedUrls[0], /pageSize=20/);
    assert.match(requestedUrls[0], /filter=5/);
  });
});
