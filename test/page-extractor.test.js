const { test } = require("node:test")
const assert = require("node:assert/strict")

const crawlTimeIr = require("../src/pipe2time.ir")
const { extractMonthsInPage } = require("../src/pipe2time.ir")

const NAMES = [
  "فروردین",
  "اردیبهشت",
  "خرداد",
  "تیر",
  "مرداد",
  "شهریور",
  "مهر",
  "آبان",
  "آذر",
  "دی",
  "بهمن",
  "اسفند",
]

// The page function is a self-contained script evaluated inside the browser.
// It is re-evaluated here against a tiny fake jQuery so its per-month isolation
// can be tested without a browser or the network.
const PAGE_YEAR = 1411
const DAY_COUNTS = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]

const makeFakeJQuery = panelTrees => {
  // A leaf wrapper exposing the handful of jQuery methods the extractor uses.
  const leaf = (data, panelContext) => ({
    length: 1,
    val: () => data.val || "",
    text: () => data.text || "",
    hasClass: name => Boolean(data.hasClass) && data.hasClass === name,
    each(fn) {
      fn.call(this, 0, this)
      return this
    },
    first: () => this,
    eq: () => this,
    find: selector => collection([], panelContext).find(selector),
  })

  const collection = (items, panelContext) => ({
    length: items.length,
    each(fn) {
      items.forEach((item, index) => fn.call(leaf(item, panelContext), index, leaf(item, panelContext)))
      return this
    },
    first() {
      return items.length ? leaf(items[0], panelContext) : leaf({}, panelContext)
    },
    eq(index) {
      return leaf(items[index] || {}, panelContext)
    },
    text: () => (items[0] && items[0].text) || "",
    hasClass: name => Boolean(items[0] && items[0].hasClass === name),
    find(selector) {
      if (!panelContext) return collection([], null)
      if (panelContext.breaks && panelContext.breaks[selector] === "throw") {
        throw new Error(`simulated failure for ${selector}`)
      }
      if (selector === ".header .jalali") {
        if (panelContext.breaks && panelContext.breaks.header === "empty") return collection([], null)
        return collection([{ text: `${NAMES[panelContext.monthNumber - 1]} 1411` }], null)
      }
      if (selector === ".dayList > div") {
        return collection(
          panelContext.days.map(day => ({ text: day.jalali, hasClass: "not-disabled" })),
          null,
        )
      }
      if (selector === ".list-unstyled li") {
        if (panelContext.breaks && panelContext.breaks.events === "none") return collection([], null)
        return collection([{ text: `1 ${NAMES[panelContext.monthNumber - 1]} offline fixture` }], null)
      }
      return collection([], null)
    },
  })

  const panelTreesWithDefaults = panelTrees.map(tree => ({ ...tree, breaks: tree.breaks || {} }))
  const $ = selector => {
    // $(this) inside an .each callback passes an already-wrapped collection.
    if (selector && typeof selector === "object" && typeof selector.each === "function") {
      return selector
    }
    if (selector === "input[type='text']") return leaf({ val: String(PAGE_YEAR) }, null)
    if (selector === ".panel-body") {
      // Panels need their own context for nested finds.
      return {
        length: panelTreesWithDefaults.length,
        each(fn) {
          panelTreesWithDefaults.forEach((tree, index) => fn.call(collection([], tree), index))
          return this
        },
      }
    }
    return collection([], null)
  }
  return $
}

const panel = (monthNumber, breaks = {}) => {
  const days = []
  for (let day = 1; day <= DAY_COUNTS[monthNumber - 1]; day++) {
    days.push({ jalali: String(day) })
  }
  return { monthNumber, days, breaks }
}

test("page extractor isolates one broken panel and reports the missing month", () => {
  // Month 4 panel is absent entirely; month 6 throws while its event list is read.
  const trees = NAMES.map((_, index) => panel(index + 1)).filter(tree => tree.monthNumber !== 4)
  trees.find(tree => tree.monthNumber === 6).breaks = { ".list-unstyled li": "throw" }

  global.$ = makeFakeJQuery(trees)
  global.window = {
    moment: value => ({
      format: target =>
        target === "YYYY/MM/DD"
          ? "2026/03/21"
          : value.replace(/^(\d+)\/(\d+)\/(\d+)$/, "$1/0$2/0$3"),
    }),
  }
  try {
    const outcome = extractMonthsInPage(PAGE_YEAR)
    assert.ok(outcome && Array.isArray(outcome.months))
    assert.equal(outcome.months.length, 12)
    assert.equal(outcome.months[3].code, "MONTH_PANEL_MISSING")
    assert.equal(outcome.months[5].ok, false)
    assert.equal(outcome.months[5].code, "MONTH_PARSE_ERROR")
    assert.match(outcome.months[5].reason, /simulated failure/)
    const successes = outcome.months
      .filter(month => month.ok === true)
      .map(month => month.index + 1)
    assert.deepEqual(successes, [1, 2, 3, 5, 7, 8, 9, 10, 11, 12])
  } finally {
    delete global.$
    delete global.window
  }
})

test("page extractor reports a page showing the wrong year instead of relabelling months", () => {
  const base = makeFakeJQuery([panel(1)])
  global.$ = selector =>
    selector === "input[type='text']"
      ? { length: 1, val: () => "1400", each(fn) { fn.call(this); return this } }
      : base(selector)
  global.window = { moment: () => ({ format: () => "2026/03/21" }) }
  try {
    const outcome = extractMonthsInPage(PAGE_YEAR)
    assert.equal(outcome.months.length, 12)
    for (const month of outcome.months) {
      assert.equal(month.code, "YEAR_MISMATCH")
      assert.match(month.reason, /not the requested year 1411/)
    }
  } finally {
    delete global.$
    delete global.window
  }
})

test("page extractor reports a missing calendar helper rather than parsing nothing", () => {
  global.$ = makeFakeJQuery([])
  global.window = {}
  try {
    const outcome = extractMonthsInPage(PAGE_YEAR)
    for (const month of outcome.months) {
      assert.equal(month.code, "EXTRACTOR_UNAVAILABLE")
    }
  } finally {
    delete global.$
    delete global.window
  }
})

test("browser-level failure surfaces as an error from extractYear", async () => {
  const { extractYear } = require("../src/pipe2time.ir")
  const brokenBrowser = {
    async newPage() {
      throw new Error("navigation failed: target page closed")
    },
  }
  await assert.rejects(() => extractYear(brokenBrowser, 1411), /target page closed/)
})

test("injectable launch path is used instead of a real browser", async t => {
  const fs = require("node:fs")
  const os = require("node:os")
  const path = require("node:path")
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "crawl-injected-"))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))

  let launched = false
  const result = await crawlTimeIr(1411, false, {
    distRoot: path.join(root, "dist"),
    apiRoot: path.join(root, "api"),
    launchBrowser: async () => {
      launched = true
      throw new Error("launch is still deterministic")
    },
  })
  assert.equal(launched, true)
  assert.equal(result.ok, false)
})
