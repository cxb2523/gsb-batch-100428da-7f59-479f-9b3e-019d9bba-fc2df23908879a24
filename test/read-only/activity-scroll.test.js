const test = require("node:test");
const assert = require("node:assert/strict");

const { scrapeActivityPage } = require("../../lib/helpers/scrape-activity");
const posts = require("../../lib/linkedin/linkedin.posts");

const CARD_SELECTOR = ".feed-shared-update-v2[data-urn]";
const COMMENT_SELECTOR =
  ".comments-comment-entity, .comments-comment-item, .comments-reply-item";
const DATE_SELECTOR =
  ".update-components-actor__sub-description, .update-components-actor__sub-description-link, time";

function fakeComment(spec) {
  return {
    querySelector(selector) {
      if (
        selector.includes("description-title") ||
        selector.includes("name-text")
      ) {
        return spec.name ? { textContent: spec.name } : null;
      }
      if (
        selector.includes("main-content") ||
        selector.includes("content-body")
      ) {
        return spec.text ? { textContent: spec.text } : null;
      }
      if (selector.startsWith("time")) {
        return spec.date ? { textContent: spec.date } : null;
      }
      if (selector === 'a[href*="/in/"]') {
        return spec.link ? { getAttribute: () => spec.link } : null;
      }
      return null;
    },
  };
}

function fakeCard(spec) {
  const actorLink = spec.actorName
    ? {
        querySelectorAll: (selector) =>
          selector === "span" ? [{ textContent: spec.actorName }] : [],
      }
    : null;
  const nodes = new Map([
    ['a[href*="/in/"]', actorLink],
    [
      ".update-components-header__text-view",
      spec.headerText ? { textContent: spec.headerText } : null,
    ],
    [DATE_SELECTOR, spec.date ? { textContent: spec.date } : null],
    [".update-components-text", spec.text ? { textContent: spec.text } : null],
  ]);
  const comments = (spec.comments || []).map(fakeComment);

  return {
    getAttribute: (name) => (name === "data-urn" ? spec.urn || "" : ""),
    querySelector: (selector) =>
      nodes.has(selector) ? nodes.get(selector) : null,
    querySelectorAll: (selector) =>
      selector === COMMENT_SELECTOR ? comments : [],
  };
}

function brokenCard(reason) {
  const fail = () => {
    throw new Error(reason);
  };
  return {
    getAttribute: fail,
    querySelector: fail,
    querySelectorAll: fail,
  };
}

function toCard(spec) {
  return spec.broken ? brokenCard(spec.reason) : fakeCard(spec);
}

// A fake read-only page whose "feed" grows as window.scrollBy runs: each
// scroll reveals the next batch of cards and grows the page height, until
// the batches run out (then the page stalls, like a real exhausted feed).
function scrollableActivityPage({
  batches,
  initialHeight = 1000,
  growPerScroll = 500,
}) {
  const state = { revealed: 1, height: initialHeight, scrolls: 0 };

  const fakeWindow = {
    innerHeight: 800,
    scrollY: 0,
    scrollBy(x, y) {
      state.scrolls += 1;
      fakeWindow.scrollY += y;
      if (state.revealed < batches.length) {
        state.revealed += 1;
        state.height += growPerScroll;
      }
    },
  };
  const fakeDocument = {
    scrollingElement: {
      clientHeight: 800,
      get scrollHeight() {
        return state.height;
      },
    },
    documentElement: {
      get scrollHeight() {
        return state.height;
      },
    },
    body: {
      get scrollHeight() {
        return state.height;
      },
    },
  };

  return {
    clicked: false,
    visited: null,
    get scrolls() {
      return state.scrolls;
    },
    frames: () => [],
    async goto(url, options) {
      this.visited = url;
      this.navigationOptions = options;
    },
    async $(selector) {
      return selector === CARD_SELECTOR ? {} : null;
    },
    async $$eval(selector, fn) {
      assert.equal(selector, CARD_SELECTOR);
      const cards = batches.slice(0, state.revealed).flat().map(toCard);
      return fn(cards);
    },
    async evaluate(fn) {
      return new Function(
        "window",
        "document",
        `return (${fn.toString()})();`
      )(fakeWindow, fakeDocument);
    },
    async click() {
      this.clicked = true;
      throw new Error("read-only scraper must not click");
    },
  };
}

function cardSpec(number, extra = {}) {
  return {
    urn: `urn:li:activity:${number}`,
    actorName: `Author ${number}`,
    headerText: `Author ${number} likes this`,
    date: `${number}h`,
    text: `Post body ${number}`,
    ...extra,
  };
}

const ACTIVITY_URL = "https://www.linkedin.com/in/example/recent-activity/all/";

test("activity scrape scrolls to fill the requested count", async () => {
  const page = scrollableActivityPage({
    batches: [
      [cardSpec(1), cardSpec(2)],
      [cardSpec(3), cardSpec(4)],
      [cardSpec(5)],
    ],
  });

  const rows = await scrapeActivityPage(page, {
    url: ACTIVITY_URL,
    count: 4,
    timeout: 0,
    scrollInterval: 0,
  });

  assert.equal(page.clicked, false);
  assert.equal(page.scrolls, 1);
  assert.deepEqual(
    rows.map((row) => row.urn),
    [
      "urn:li:activity:1",
      "urn:li:activity:2",
      "urn:li:activity:3",
      "urn:li:activity:4",
    ]
  );
  assert.equal(rows.outcome.returned, 4);
  assert.equal(rows.outcome.requested, 4);
  assert.equal(rows.outcome.scrolls, 1);
  assert.equal(rows.outcome.stopReason, "count_satisfied");
  assert.equal(rows.outcome.stoppedEarly, false);
  assert.equal(rows.outcome.skippedCount, 0);
});

test("activity scrape stops on a stalled page and reports it", async () => {
  const page = scrollableActivityPage({
    batches: [[cardSpec(1), cardSpec(2)]],
  });

  const rows = await scrapeActivityPage(page, {
    url: ACTIVITY_URL,
    count: 5,
    timeout: 0,
    scrollInterval: 0,
    maxScrolls: 10,
  });

  assert.equal(page.clicked, false);
  assert.equal(rows.length, 2);
  assert.equal(rows.outcome.returned, 2);
  assert.equal(rows.outcome.stopReason, "page_stalled");
  assert.equal(rows.outcome.stoppedEarly, true);
  assert.equal(rows.outcome.pageStalled, true);
  assert.equal(rows.outcome.scrollLimitReached, false);
  assert.ok(rows.outcome.scrolls < 10, "stall stops before the scroll cap");
});

test("activity scrape stops at the scroll cap and reports it", async () => {
  const page = scrollableActivityPage({
    batches: [
      [cardSpec(1)],
      [cardSpec(2)],
      [cardSpec(3)],
      [cardSpec(4)],
      [cardSpec(5)],
      [cardSpec(6)],
    ],
  });

  const rows = await scrapeActivityPage(page, {
    url: ACTIVITY_URL,
    count: 10,
    timeout: 0,
    scrollInterval: 0,
    maxScrolls: 3,
  });

  assert.equal(page.clicked, false);
  assert.equal(page.scrolls, 3);
  assert.equal(rows.length, 4);
  assert.equal(rows.outcome.returned, 4);
  assert.equal(rows.outcome.scrolls, 3);
  assert.equal(rows.outcome.stopReason, "scroll_limit");
  assert.equal(rows.outcome.stoppedEarly, true);
  assert.equal(rows.outcome.scrollLimitReached, true);
  assert.equal(rows.outcome.pageStalled, false);
});

test("activity scrape skips broken cards and keeps the rest", async () => {
  const page = scrollableActivityPage({
    batches: [
      [
        cardSpec(1),
        { broken: true, reason: "broken card: detached DOM node" },
        cardSpec(3),
      ],
    ],
  });

  const rows = await scrapeActivityPage(page, {
    url: ACTIVITY_URL,
    count: 2,
    timeout: 0,
    scrollInterval: 0,
  });

  assert.equal(page.clicked, false);
  assert.deepEqual(
    rows.map((row) => row.urn),
    ["urn:li:activity:1", "urn:li:activity:3"]
  );
  assert.equal(rows[0].text, "Post body 1");
  assert.equal(rows[1].text, "Post body 3");
  assert.equal(rows.outcome.skippedCount, 1);
  assert.equal(rows.outcome.skipped[0].index, 1);
  assert.match(rows.outcome.skipped[0].reason, /broken card: detached DOM node/);
});

test("activity scrape keeps zero and negative counts on the single-pass path", async () => {
  const zeroPage = scrollableActivityPage({
    batches: [[cardSpec(1), cardSpec(2)], [cardSpec(3)]],
  });
  const zeroRows = await scrapeActivityPage(zeroPage, {
    url: ACTIVITY_URL,
    count: 0,
    timeout: 0,
    scrollInterval: 0,
  });
  assert.deepEqual(zeroRows, []);
  assert.equal(zeroPage.scrolls, 0);
  assert.equal(zeroRows.outcome.returned, 0);

  const negativePage = scrollableActivityPage({
    batches: [[cardSpec(1), cardSpec(2)], [cardSpec(3)]],
  });
  const negativeRows = await scrapeActivityPage(negativePage, {
    url: ACTIVITY_URL,
    count: -5,
    timeout: 0,
    scrollInterval: 0,
  });
  assert.equal(negativeRows.length, 2);
  assert.equal(negativePage.scrolls, 0);
  assert.equal(negativeRows.outcome.returned, 2);
});

test("activity outcome is attached without changing the result shape", async () => {
  const page = scrollableActivityPage({
    batches: [[cardSpec(1), cardSpec(2)]],
  });

  const rows = await scrapeActivityPage(page, {
    url: ACTIVITY_URL,
    count: 2,
    timeout: 0,
    scrollInterval: 0,
  });

  assert.equal(Object.keys(rows).includes("outcome"), false);
  assert.equal(JSON.stringify(rows).includes("stopReason"), false);
  assert.equal(typeof rows.outcome, "object");

  const projected = await posts(
    scrollableActivityPage({ batches: [[cardSpec(1), cardSpec(2)]] }),
    null,
    { user: "https://www.linkedin.com/in/example/", count: 2 }
  );
  assert.deepEqual(Object.keys(projected), ["0", "1"]);
  assert.equal(projected.outcome.returned, 2);
  assert.equal(projected.outcome.stopReason, "count_satisfied");
});
