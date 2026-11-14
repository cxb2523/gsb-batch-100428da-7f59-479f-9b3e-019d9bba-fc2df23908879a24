const { test } = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")

const {
  MONTH_COUNT,
  STATUS,
  validateMonthData,
  finalizeYear,
  exportYear,
} = require("../src/year-collection")

const DAY_COUNTS = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]

const pad2 = value => String(value).padStart(2, "0")

// Builds the same shape the page extractor produces, with a real Jalali-like
// day grid and at least one event, without touching the network.
const makeMonth = (year, monthNumber, options = {}) => {
  const days = options.days || DAY_COUNTS[monthNumber - 1]
  const weeks = []
  // A couple of disabled leading cells exercise the padding behaviour.
  for (let pad = 2; pad > 0; pad--) {
    weeks.push({
      holiday: false,
      disabled: true,
      day: { j: pad === 2 ? "30" : "31", m: "1", q: "1" },
    })
  }
  for (let day = 1; day <= days; day++) {
    weeks.push({
      holiday: options.holidayDay === day,
      disabled: false,
      day: { j: String(day), m: String((day % 30) + 1), q: String((day % 29) + 1) },
    })
  }
  while (weeks.length % 7 !== 0) {
    weeks.push({ holiday: false, disabled: true, day: { j: String(weeks.length), m: "2", q: "2" } })
  }

  const events =
    options.events === undefined
      ? [
          {
            isHoliday: false,
            text: `${monthNumber} ${dayLabel(monthNumber)} sample event`,
            jDate: `${year}/${pad2(monthNumber)}/01`,
            mDate: `2026/0${Math.min(monthNumber, 9)}/01`,
            jDay: "1",
          },
        ]
      : options.events

  return {
    header: {
      miladi: options.miladi || "March - April 2026",
      qamari: options.qamari || "شوال - ذوالقعده - 1447",
      jalali: options.jalali || `${dayLabel(monthNumber)} ${year}`,
    },
    events,
    weeks,
  }
}

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
const dayLabel = monthNumber => NAMES[monthNumber - 1]

const completeYear = year =>
  Array.from({ length: MONTH_COUNT }, (_, index) => ({
    index,
    ok: true,
    data: makeMonth(year, index + 1),
  }))

const tempRoots = t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "year-collection-"))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  return {
    root,
    dist: path.join(root, "dist"),
    api: path.join(root, "api"),
  }
}

test("all twelve months succeed: report is complete and both outputs are written", t => {
  const roots = tempRoots(t)
  const year = 1405
  const report = finalizeYear(year, completeYear(year))

  assert.equal(report.status, "complete")
  assert.equal(report.counts.success, 12)
  assert.equal(report.counts.failed, 0)
  assert.equal(report.counts.notAttempted, 0)
  assert.deepEqual(
    report.months.map(month => month.status),
    Array.from({ length: 12 }, () => STATUS.SUCCESS),
  )
  assert.deepEqual(report.failedMonths, [])
  assert.deepEqual(report.notAttemptedMonths, [])
  assert.equal(report.collected.length, 12)

  const exported = exportYear(report, { api: true, distRoot: roots.dist, apiRoot: roots.api })
  assert.equal(exported.api.exported, true)
  assert.equal(exported.api.reason, null)

  // API files exactly mirror every collected month in calendar order.
  const apiIndex = JSON.parse(fs.readFileSync(path.join(roots.api, "1405/index.json"), "utf8"))
  assert.deepEqual(apiIndex[1405], report.collected)
  const events = JSON.parse(fs.readFileSync(path.join(roots.api, "1405/events.json"), "utf8"))
  const weeks = JSON.parse(fs.readFileSync(path.join(roots.api, "1405/weeks.json"), "utf8"))
  assert.deepEqual(
    events,
    report.collected.flatMap(month => month.events),
  )
  assert.deepEqual(
    weeks,
    report.collected.flatMap(month => month.weeks),
  )

  const calendarEntry = JSON.parse(fs.readFileSync(path.join(roots.dist, "outcome-1405.json"), "utf8"))
  assert.equal(calendarEntry.status, "complete")
  const apiOutcome = JSON.parse(fs.readFileSync(path.join(roots.api, "1405/outcome.json"), "utf8"))
  assert.equal(apiOutcome.status, "complete")
})

test("partial failure keeps successful months and reports why the rest are missing", t => {
  const roots = tempRoots(t)
  const year = 1406
  const entries = completeYear(year)

  // Month 2 has an empty grid (page structure changed).
  entries[1] = {
    index: 1,
    ok: true,
    data: makeMonth(year, 2, { events: [] }),
  }
  // Month 5 panel is missing entirely.
  entries[4] = {
    index: 4,
    ok: false,
    code: "MONTH_PANEL_MISSING",
    reason: "Month 5 of 1406 has no calendar panel on the page",
  }
  // Month 9 was never attempted (year-level extraction stopped first).
  entries[8] = undefined

  const report = finalizeYear(year, entries)
  assert.equal(report.status, "partial")
  assert.deepEqual(report.counts, { success: 9, failed: 2, notAttempted: 1 })
  assert.deepEqual(report.failedMonths, [2, 5])
  assert.deepEqual(report.notAttemptedMonths, [9])
  assert.deepEqual(
    report.months.filter(month => month.status === STATUS.SUCCESS).map(month => month.month),
    [1, 3, 4, 6, 7, 8, 10, 11, 12],
  )

  // Every unresolved month carries a readable reason, and none of them is a
  // success pretending to be an empty month.
  for (const month of report.months) {
    if (month.status !== STATUS.SUCCESS) {
      assert.equal(typeof month.reason, "string")
      assert.ok(month.reason.length > 0, `month ${month.month} has no reason`)
      assert.equal(month.data, undefined)
    }
  }
  assert.match(report.months[1].reason, /expected at least 28|events is empty|structure/)

  const exported = exportYear(report, { api: true, distRoot: roots.dist, apiRoot: roots.api })
  assert.equal(exported.written.calendarEntry, true)
  assert.equal(exported.api.exported, true)
  assert.equal(exported.api.index, true)
  assert.equal(exported.api.events, false)
  assert.equal(exported.api.weeks, false)
  assert.match(exported.api.reason, /events\.json and weeks\.json were not exported/)
  assert.match(exported.api.reason, /2, 5, 9/)

  const apiIndex = JSON.parse(fs.readFileSync(path.join(roots.api, "1406/index.json"), "utf8"))
  assert.equal(apiIndex[1406].length, 9, "only collected months are exported")
  assert.deepEqual(apiIndex[1406], report.collected)
  assert.equal(fs.existsSync(path.join(roots.api, "1406/events.json")), false)
  assert.equal(fs.existsSync(path.join(roots.api, "1406/weeks.json")), false)

  const outcome = JSON.parse(fs.readFileSync(path.join(roots.api, "1406/outcome.json"), "utf8"))
  assert.equal(outcome.status, "partial")
  assert.match(outcome.reason, /unavailable months: 2, 5, 9/)
  assert.deepEqual(
    outcome.months.map(month => month.status),
    [
      "success",
      "failed",
      "success",
      "success",
      "failed",
      "success",
      "success",
      "success",
      "not-attempted",
      "success",
      "success",
      "success",
    ],
  )
})

test("all twelve months fail: nothing is written and the result is an explicit failure", t => {
  const roots = tempRoots(t)
  const year = 1407
  const entries = Array.from({ length: 12 }, (_, index) => ({
    index,
    ok: false,
    code: "MONTH_PANEL_MISSING",
    reason: `Month ${index + 1} of 1407 has no calendar panel on the page`,
  }))

  const report = finalizeYear(year, entries)
  assert.equal(report.status, "failed")
  assert.deepEqual(report.counts, { success: 0, failed: 12, notAttempted: 0 })
  assert.equal(report.collected.length, 0)
  assert.match(report.reason, /No month of 1407 could be collected/)

  const exported = exportYear(report, { api: true, distRoot: roots.dist, apiRoot: roots.api })
  assert.equal(exported.calendarEntry, null)
  assert.equal(exported.written.calendarEntry, false)
  assert.equal(exported.api.exported, false)
  assert.equal(exported.api.reason, "No API files were written for 1407: no month could be collected")
  assert.equal(fs.existsSync(path.join(roots.dist, "calendar.json")), false)
  assert.equal(fs.existsSync(path.join(roots.api, "1407")), false)
  assert.equal(fs.existsSync(roots.dist), false)
})

test("a year that was never attempted reports every month as not attempted", () => {
  const report = finalizeYear(1408, [], {
    notAttemptedReason: "Year 1408 was not attempted: extraction failed before any month was read",
  })
  assert.equal(report.status, "failed")
  assert.deepEqual(report.counts, { success: 0, failed: 0, notAttempted: 12 })
  assert.equal(report.months.length, 12)
  for (const month of report.months) {
    assert.equal(month.status, STATUS.NOT_ATTEMPTED)
    assert.match(month.reason, /extraction failed/)
  }
  assert.match(report.reason, /extraction failed/)
})

test("validateMonthData rejects a page whose grids or events shifted", () => {
  const year = 1409
  assert.deepEqual(validateMonthData(year, 1, makeMonth(year, 1)), [])

  const emptyGrid = makeMonth(year, 1, { days: 31 })
  emptyGrid.weeks = []
  assert.ok(validateMonthData(year, 1, emptyGrid).some(problem => /weeks has 0 cells/.test(problem)))

  const shifted = makeMonth(year, 2)
  // Drop the first enabled day, which shifts all following day numbers up.
  shifted.weeks.splice(
    shifted.weeks.findIndex(cell => cell.disabled === false),
    1,
  )
  assert.ok(
    validateMonthData(year, 2, shifted).some(problem => /is day 2, expected 1|enabled day cells/.test(problem)),
  )

  const wrongMonthEvents = makeMonth(year, 3)
  wrongMonthEvents.events = [
    { isHoliday: false, text: "wrong month", jDate: "1409/04/01", mDate: "2026/06/01", jDay: "1" },
  ]
  assert.ok(
    validateMonthData(year, 3, wrongMonthEvents).some(problem =>
      /expected 1409\/03\/DD/.test(problem),
    ),
  )

  const missingHeader = makeMonth(year, 4)
  missingHeader.header.jalali = " "
  assert.ok(validateMonthData(year, 4, missingHeader).some(problem => /header\.jalali/.test(problem)))
})

test("finalizeYear accepts extraction objects and preserves panel-level errors", () => {
  const year = 1410
  const months = completeYear(year).map(entry => entry.data)
  months[2] = { ...months[2], events: [] }
  const panelErrors = [{ index: 12, code: "MONTH_UNIDENTIFIED", reason: "Panel 13 names no month" }]

  const report = finalizeYear(year, { months, panelErrors })
  assert.equal(report.counts.success, 11)
  assert.equal(report.months[2].status, STATUS.FAILED)
  assert.deepEqual(report.panelErrors, panelErrors)
})

test("finalizeYear is defensive about malformed raw input", () => {
  const report = finalizeYear(1405, [
    null,
    { index: 1, header: {}, events: [], weeks: [] },
    { index: 2, data: null },
    "not-an-object",
  ])
  assert.equal(report.months.length, 12)
  // Present-but-unusable entries were attempted and failed; holes were never tried.
  assert.equal(report.counts.failed, 3)
  assert.equal(report.counts.notAttempted, 9)
  for (const month of report.months) {
    assert.ok(month.reason && month.reason.length > 0)
  }
})
