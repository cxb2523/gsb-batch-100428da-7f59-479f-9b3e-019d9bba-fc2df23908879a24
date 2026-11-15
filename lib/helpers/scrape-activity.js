const {
  findFirstSelector,
  waitForPageContext,
} = require("./find-page-context");
const { sleep: defaultSleep } = require("../interactions/timing");
const selectors = require("../selectors/read-only");

const DEFAULT_SCROLL_INTERVAL_MS = 800;
const DEFAULT_MAX_SCROLLS = 10;
const MAX_SCROLL_INTERVAL_MS = 10000;
const MAX_SCROLLS_LIMIT = 50;

function activityUrlFromRow(row) {
  const urn = String((row && row.urn) || "").trim();
  if (/^urn:li:(?:activity|share):\d+$/.test(urn)) {
    return `https://www.linkedin.com/feed/update/${urn}/`;
  }

  try {
    const url = new URL(String((row && row.url) || ""));
    if (
      url.protocol === "https:" &&
      url.hostname === "www.linkedin.com" &&
      (url.pathname.startsWith("/feed/update/") ||
        url.pathname.startsWith("/posts/"))
    ) {
      url.search = "";
      url.hash = "";
      return url.href;
    }
  } catch (_) {
    // A row without a canonical LinkedIn URL is ignored by normalization.
  }

  return "";
}

function normalizeActivityRows(rows, count) {
  const uniqueRows = [];
  const seen = new Set();

  for (const row of rows) {
    const url = activityUrlFromRow(row);
    const key = String(row.urn || url).trim();
    if (!key || seen.has(key)) {
      continue;
    }

    seen.add(key);
    uniqueRows.push({ ...row, url });
  }

  if (!Number.isFinite(count) || count < 0) {
    return uniqueRows;
  }

  return count === 0 ? [] : uniqueRows.slice(0, count);
}

// Runs inside the page. Must stay self-contained (no closures) so Puppeteer
// can serialize it. Each card is parsed in isolation: a malformed card is
// skipped with a readable reason instead of failing the whole batch.
function parseActivityCardsInPage(cards) {
  const clean = (value) => String(value || "").trim().replace(/\s+/g, " ");

  const parseCard = (card) => {
    const actorLink = card.querySelector('a[href*="/in/"]');
    const spanTexts = actorLink
      ? Array.from(actorLink.querySelectorAll("span"))
          .map((span) => clean(span.textContent))
          .filter((text) => text && !text.includes("•"))
      : [];
    const frequencies = spanTexts.reduce((counts, text) => {
      counts[text] = (counts[text] || 0) + 1;
      return counts;
    }, {});
    const actorName =
      spanTexts
        .filter((text) => frequencies[text] > 1)
        .sort((left, right) => left.length - right.length)[0] ||
      spanTexts.sort((left, right) => left.length - right.length)[0] ||
      "";
    const header = card.querySelector(".update-components-header__text-view");
    const date = card.querySelector(
      ".update-components-actor__sub-description, .update-components-actor__sub-description-link, time"
    );
    const text = card.querySelector(".update-components-text");
    const comments = Array.from(
      card.querySelectorAll(
        ".comments-comment-entity, .comments-comment-item, .comments-reply-item"
      )
    ).map((comment) => {
      const name = comment.querySelector(
        ".comments-comment-meta__description-title, .comments-post-meta__name-text"
      );
      const content = comment.querySelector(
        ".comments-comment-item__main-content, .comments-comment-item-content-body, .comments-reply-item-content-body"
      );
      const commentDate = comment.querySelector(
        "time.comments-comment-meta__data, time"
      );
      const profileLink = comment.querySelector('a[href*="/in/"]');

      return {
        name: clean(name && name.textContent),
        text: clean(content && content.textContent),
        date: clean(commentDate && commentDate.textContent),
        link: profileLink ? profileLink.getAttribute("href") || "" : "",
      };
    });

    return {
      urn: card.getAttribute("data-urn") || "",
      actorName,
      headerText: clean(header && header.textContent),
      date: clean(date && date.textContent),
      text: clean(text && text.textContent),
      comments,
    };
  };

  const rows = [];
  const skipped = [];
  cards.forEach((card, index) => {
    try {
      rows.push(parseCard(card));
    } catch (error) {
      skipped.push({
        index,
        reason: String((error && error.message) || error || "card parse failed"),
      });
    }
  });

  return { rows, skipped };
}

async function parseActivityCards(context, selector) {
  const parsed = await context.$$eval(selector, parseActivityCardsInPage);
  if (Array.isArray(parsed)) {
    // Legacy doubles that resolve $$eval to a bare row array.
    return { rows: parsed, skipped: [] };
  }

  return {
    rows: Array.isArray(parsed && parsed.rows) ? parsed.rows : [],
    skipped: Array.isArray(parsed && parsed.skipped) ? parsed.skipped : [],
  };
}

// Read-only scrolling: window.scrollBy only, never clicks cards or buttons.
async function scrollActivityPageOnce(page) {
  await page.evaluate(() => {
    const scrollingElement = document.scrollingElement || document.documentElement;
    const step = window.innerHeight || scrollingElement.clientHeight || 800;
    window.scrollBy(0, step);
  });
}

async function activityPageHeight(page) {
  const height = await page.evaluate(() => {
    const scrollingElement = document.scrollingElement || document.documentElement;
    return Math.max(
      Number(scrollingElement.scrollHeight) || 0,
      (document.body && Number(document.body.scrollHeight)) || 0
    );
  });
  return Number(height) || 0;
}

function boundedOption(value, fallback, min, max) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, number));
}

// The run outcome rides on the result as a non-enumerable `outcome` field so
// existing callers (map/deep-equal/JSON) keep working unchanged while the
// scrape run stays auditable via `result.outcome`.
function attachOutcome(target, outcome) {
  if (target && typeof target === "object" && outcome) {
    Object.defineProperty(target, "outcome", {
      value: outcome,
      enumerable: false,
      configurable: true,
      writable: true,
    });
  }
  return target;
}

function buildOutcome({
  requested,
  returned,
  scrolls,
  stopReason,
  skipped,
}) {
  const scrollLimitReached = stopReason === "scroll_limit";
  const pageStalled = stopReason === "page_stalled";
  return {
    requested,
    returned,
    scrolls,
    stopReason,
    stoppedEarly: scrollLimitReached || pageStalled,
    scrollLimitReached,
    pageStalled,
    skippedCount: skipped.length,
    skipped: skipped.map((entry) => ({ index: entry.index, reason: entry.reason })),
  };
}

async function scrapeActivityPage(
  page,
  {
    url,
    count = 20,
    timeout = 10000,
    scrollInterval = DEFAULT_SCROLL_INTERVAL_MS,
    maxScrolls = DEFAULT_MAX_SCROLLS,
    sleep = defaultSleep,
  } = {}
) {
  await page.goto(url, {
    waitUntil: "domcontentloaded",
    timeout: 60000,
  });

  const context = await waitForPageContext(page, selectors.activity.cards, {
    timeout,
  });
  if (!context) {
    return attachOutcome([], buildOutcome({
      requested: count,
      returned: 0,
      scrolls: 0,
      stopReason: "no_cards",
      skipped: [],
    }));
  }

  const selector = await findFirstSelector(context, selectors.activity.cards);
  if (!selector) {
    return attachOutcome([], buildOutcome({
      requested: count,
      returned: 0,
      scrolls: 0,
      stopReason: "no_cards",
      skipped: [],
    }));
  }

  // Only a positive, finite count asks for more cards; zero/negative/NaN
  // counts keep the historical single-pass behavior.
  const target = Number.isFinite(count) && count > 0 ? Math.floor(count) : 0;
  const interval = boundedOption(
    scrollInterval,
    DEFAULT_SCROLL_INTERVAL_MS,
    0,
    MAX_SCROLL_INTERVAL_MS
  );
  const scrollLimit = Math.floor(
    boundedOption(maxScrolls, DEFAULT_MAX_SCROLLS, 0, MAX_SCROLLS_LIMIT)
  );

  const skippedByIndex = new Map();
  const collect = (parsed) => {
    for (const entry of parsed.skipped) {
      if (!skippedByIndex.has(entry.index)) {
        skippedByIndex.set(entry.index, entry);
      }
    }
  };

  let parsed = await parseActivityCards(context, selector);
  collect(parsed);
  let rows = parsed.rows;
  let uniqueCount = normalizeActivityRows(rows, -1).length;

  const canScroll = typeof page.evaluate === "function";
  let scrolls = 0;
  let stopReason = "count_satisfied";

  if (target > 0 && uniqueCount < target && !canScroll) {
    stopReason = "scroll_unavailable";
  }

  let lastHeight = 0;
  if (target > 0 && uniqueCount < target && canScroll) {
    try {
      lastHeight = await activityPageHeight(page);
    } catch (_) {
      stopReason = "scroll_unavailable";
    }
  }

  while (
    stopReason === "count_satisfied" &&
    target > 0 &&
    uniqueCount < target &&
    scrolls < scrollLimit
  ) {
    try {
      await scrollActivityPageOnce(page);
    } catch (_) {
      stopReason = "page_stalled";
      break;
    }
    scrolls += 1;
    if (interval > 0) {
      await sleep(interval);
    }

    let height = lastHeight;
    try {
      height = await activityPageHeight(page);
      parsed = await parseActivityCards(context, selector);
    } catch (_) {
      stopReason = "page_stalled";
      break;
    }
    collect(parsed);
    rows = parsed.rows;
    uniqueCount = normalizeActivityRows(rows, -1).length;

    if (height <= lastHeight) {
      stopReason = "page_stalled";
      break;
    }
    lastHeight = height;
  }

  if (
    stopReason === "count_satisfied" &&
    target > 0 &&
    uniqueCount < target &&
    scrolls >= scrollLimit
  ) {
    stopReason = "scroll_limit";
  }

  const result = normalizeActivityRows(rows, count);
  return attachOutcome(result, buildOutcome({
    requested: count,
    returned: result.length,
    scrolls,
    stopReason,
    skipped: [...skippedByIndex.values()].sort((a, b) => a.index - b.index),
  }));
}

module.exports = {
  activityUrlFromRow,
  attachOutcome,
  normalizeActivityRows,
  scrapeActivityPage,
  scrollDefaults: Object.freeze({
    scrollInterval: DEFAULT_SCROLL_INTERVAL_MS,
    maxScrolls: DEFAULT_MAX_SCROLLS,
    maxScrollInterval: MAX_SCROLL_INTERVAL_MS,
    maxScrollsLimit: MAX_SCROLLS_LIMIT,
  }),
};
