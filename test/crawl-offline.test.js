const { test } = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")

const crawlTimeIr = require("../src/pipe2time.ir")

const NAMES = [
  "فروردین",
  "اردیبهشت",
  "خرداد",
  "تیر",
  "امرداد",
  "شهریور",
  "مهر",
  "آبان",
  "آذر",
  "دی",
  "بهمن",
  "اسفند",
]
const DAY_COUNTS = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]
const pad2 = value => String(value).padStart(2, "0")

// Minimal offline stand-in for one extracted month panel.
const makeMonth = (year, monthNumber) => {
  const weeks = []
  for (let day = 1; day <= DAY_COUNTS[monthNumber - 1]; day++) {
    weeks.push({
      holiday: false,
      disabled: false,
      day: { j: String(day), m: String(day), q: String(day) },
    })
  }
  return {
    header: {
      miladi: "March - April 2026",
      qamari: "شوال - ذوالقعده - 1447",
      jalali: `${NAMES[monthNumber - 1]} ${year}`,
    },
    events: [
      {
        isHoliday: false,
        text: `${monthNumber} ${NAMES[monthNumber - 1]} offline sample`,
        jDate: `${year}/${pad2(monthNumber)}/01`,
        mDate: "2026/03/21",
        jDay: "1",
      },
    ],
    weeks,
  }
}

const fakeBrowser = () => ({ closed: false, async close() { this.closed = true } })

const makeExtractor = handler => {
  const calls = []
  const extract = async (browser, year) => {
    calls.push(Number(year))
    return handler(Number(year))
  }
  return { calls, extract }
}

const tempRoot = t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "crawl-offline-"))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  return { distRoot: path.join(root, "dist"), apiRoot: path.join(root, "api") }
}

const readJson = file => JSON.parse(fs.readFileSync(file, "utf8"))

test("complete year: writes calendar.json and all API files, closes the browser", async t => {
  const roots = tempRoot(t)
  const browser = fakeBrowser()
  const { calls, extract } = makeExtractor(year => ({
    months: Array.from({ length: 12 }, (_, index) => ({
      index,
      ok: true,
      data: makeMonth(year, index + 1),
    })),
    panelErrors: [],
  }))

  const result = await crawlTimeIr(1405, true, {
    ...roots,
    launchBrowser: async () => browser,
    extractYear: extract,
  })

  assert.equal(browser.closed, true)
  assert.deepEqual(calls, [1405])
  assert.equal(result.ok, true)
  assert.equal(result.status, "complete")
  assert.equal(result.failedYears.length, 0)
  assert.equal(result.years.length, 1)
  assert.equal(result.years[0].counts.success, 12)
  assert.equal(result.years[0].files.events, true)

  const calendar = readJson(path.join(roots.distRoot, "calendar.json"))
  assert.equal(calendar[1405].length, 12)
  assert.equal(readJson(path.join(roots.apiRoot, "1405/events.json")).length, 12)
  assert.equal(readJson(path.join(roots.apiRoot, "1405/weeks.json")).length, DAY_COUNTS.reduce((a, b) => a + b))
})

test("partial failure: surviving months are written, missing months are reported", async t => {
  const roots = tempRoot(t)
  const { extract } = makeExtractor(year => {
    const months = Array.from({ length: 12 }, (_, index) => ({
      index,
      ok: true,
      data: makeMonth(year, index + 1),
    }))
    months[5] = {
      index: 5,
      ok: false,
      code: "MONTH_PARSE_ERROR",
      reason: "Month 6 of 1406 could not be parsed: selector .list-unstyled li matched no events",
    }
    months[10] = undefined
    return { months, panelErrors: [] }
  })

  const result = await crawlTimeIr(1406, true, {
    ...roots,
    launchBrowser: async () => fakeBrowser(),
    extractYear: extract,
  })

  assert.equal(result.ok, true)
  assert.equal(result.status, "partial")
  assert.equal(result.years[0].counts.success, 10)
  assert.deepEqual(result.years[0].failedMonths, [6])
  assert.deepEqual(result.years[0].notAttemptedMonths, [11])

  const calendar = readJson(path.join(roots.distRoot, "calendar.json"))
  assert.equal(calendar[1406].length, 10, "calendar.json holds only the collected months")

  const index = readJson(path.join(roots.apiRoot, "1406/index.json"))
  assert.equal(index[1406].length, 10)
  assert.equal(fs.existsSync(path.join(roots.apiRoot, "1406/events.json")), false)
  assert.equal(fs.existsSync(path.join(roots.apiRoot, "1406/weeks.json")), false)

  const outcome = readJson(path.join(roots.apiRoot, "1406/outcome.json"))
  assert.match(outcome.api.reason, /require all 12 months/)
  assert.match(outcome.months[5].reason, /selector/)
  assert.equal(outcome.months[10].status, "not-attempted")
})

test("API switch off: calendar data is kept but nothing is written under api/", async t => {
  const roots = tempRoot(t)
  const { extract } = makeExtractor(year => ({
    months: Array.from({ length: 12 }, (_, index) => ({
      index,
      ok: true,
      data: makeMonth(year, index + 1),
    })),
  }))

  const result = await crawlTimeIr(1405, false, {
    ...roots,
    launchBrowser: async () => fakeBrowser(),
    extractYear: extract,
  })

  assert.equal(result.years[0].api.requested, false)
  assert.equal(result.years[0].api.exported, false)
  assert.equal(fs.existsSync(roots.apiRoot), false)
  assert.equal(readJson(path.join(roots.distRoot, "calendar.json"))[1405].length, 12)
})

test("every month fails: no output files, explicit failure result", async t => {
  const roots = tempRoot(t)
  const { extract } = makeExtractor(() => ({
    months: Array.from({ length: 12 }, (_, index) => ({
      index,
      ok: false,
      code: "MONTH_PANEL_MISSING",
      reason: `Month ${index + 1} has no calendar panel on the page`,
    })),
  }))

  const result = await crawlTimeIr(1407, true, {
    ...roots,
    launchBrowser: async () => fakeBrowser(),
    extractYear: extract,
  })

  assert.equal(result.ok, false)
  assert.equal(result.status, "failed")
  assert.equal(result.calendarPath, null)
  assert.equal(result.failedYears.length, 1)
  assert.equal(result.failedYears[0].year, 1407)
  assert.match(result.reason, /No year could be collected/)

  assert.equal(fs.existsSync(path.join(roots.distRoot, "calendar.json")), false)
  assert.equal(fs.existsSync(roots.distRoot), false, "no outcome files for a failed year either")
  assert.equal(fs.existsSync(path.join(roots.apiRoot, "1407")), false)
})

test("a year-level extraction error is reported as not attempted, not as month failures", async t => {
  const roots = tempRoot(t)
  const { extract } = makeExtractor(() => {
    throw new Error("net::ERR_CONNECTION_RESET")
  })

  const result = await crawlTimeIr(1408, true, {
    ...roots,
    launchBrowser: async () => fakeBrowser(),
    extractYear: extract,
  })

  assert.equal(result.status, "failed")
  assert.equal(result.years[0].counts.notAttempted, 12)
  assert.equal(result.years[0].counts.failed, 0)
  assert.match(result.years[0].months[0].reason, /ERR_CONNECTION_RESET/)
  assert.equal(fs.existsSync(roots.distRoot), false)
})

test("browser launch failure yields an explicit failure instead of a silent success", async t => {
  const roots = tempRoot(t)
  const result = await crawlTimeIr(1405, true, {
    ...roots,
    launchBrowser: async () => {
      throw new Error("Could not find Chromium")
    },
    extractYear: async () => {
      throw new Error("should not be called")
    },
  })

  assert.equal(result.ok, false)
  assert.equal(result.status, "failed")
  assert.match(result.failedYears[0].reason, /browser could not start/)
  assert.equal(fs.existsSync(roots.distRoot), false)
})

test("multiple years: partial and complete survive together, failed year writes nothing", async t => {
  const roots = tempRoot(t)
  const { calls, extract } = makeExtractor(year => {
    if (year === 1409) {
      return {
        months: Array.from({ length: 12 }, (_, index) => ({
          index,
          ok: false,
          code: "MONTH_PANEL_MISSING",
          reason: "page structure changed",
        })),
      }
    }
    const months = Array.from({ length: 12 }, (_, index) => ({
      index,
      ok: true,
      data: makeMonth(year, index + 1),
    }))
    if (year === 1410) months[0] = { index: 0, ok: false, code: "MONTH_PARSE_ERROR", reason: "panel broken" }
    return { months }
  })

  const result = await crawlTimeIr("1409, 1410", true, {
    ...roots,
    launchBrowser: async () => fakeBrowser(),
    extractYear: extract,
  })

  assert.deepEqual(calls, [1409, 1410])
  assert.equal(result.ok, true)
  assert.equal(result.status, "partial")
  assert.deepEqual(
    result.years.map(year => year.status),
    ["failed", "partial"],
  )
  assert.deepEqual(
    result.failedYears.map(year => year.year),
    [1409],
  )

  const calendar = readJson(path.join(roots.distRoot, "calendar.json"))
  assert.deepEqual(Object.keys(calendar), ["1410"])
  assert.equal(calendar[1410].length, 11)
  assert.equal(fs.existsSync(path.join(roots.apiRoot, "1409")), false)
  assert.equal(readJson(path.join(roots.apiRoot, "1410/index.json"))[1410].length, 11)
})

test("no years requested is rejected without touching the filesystem", async t => {
  const roots = tempRoot(t)
  const result = await crawlTimeIr([], true, {
    ...roots,
    launchBrowser: async () => {
      throw new Error("should not be called")
    },
  })
  assert.equal(result.ok, false)
  assert.equal(result.status, "failed")
  assert.equal(fs.existsSync(roots.distRoot), false)
})
