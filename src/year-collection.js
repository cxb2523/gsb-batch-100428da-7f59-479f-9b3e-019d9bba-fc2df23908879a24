const fs = require("node:fs")
const path = require("node:path")
const { createJsonApiFile } = require("./year-api")

const MONTH_COUNT = 12

// Each month ends in exactly one of these states. "not-attempted" is distinct
// from failure on purpose: it says a year-level problem stopped extraction
// before that month was ever looked at, not that the month page was broken.
const STATUS = Object.freeze({
  SUCCESS: "success",
  FAILED: "failed",
  NOT_ATTEMPTED: "not-attempted",
})

const isNonEmptyString = value => typeof value === "string" && value.trim() !== ""
const pad2 = value => String(value).padStart(2, "0")

const parseGregorian = value => {
  const match = /^(\d{4})\/(\d{2})\/(\d{2})$/.exec(value)
  if (!match) return null
  const parts = match.slice(1).map(Number)
  const date = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]))
  if (
    date.getUTCFullYear() !== parts[0] ||
    date.getUTCMonth() + 1 !== parts[1] ||
    date.getUTCDate() !== parts[2]
  ) {
    return null
  }
  return parts
}

// Structural checks for one month. They are deliberately strict about things a
// half-changed page silently gets wrong (empty grids, shifted day numbers,
// events attributed to another month) so a broken month is reported instead of
// being exported as an empty-but-successful month.
const validateMonthData = (year, monthNumber, month) => {
  const problems = []
  if (!month || typeof month !== "object" || Array.isArray(month)) {
    return ["month data is not an object"]
  }

  const header = month.header
  if (!header || typeof header !== "object" || Array.isArray(header)) {
    problems.push("header is missing")
  } else {
    for (const key of ["miladi", "qamari", "jalali"]) {
      if (!isNonEmptyString(header[key])) problems.push(`header.${key} is missing or empty`)
    }
  }

  const weeks = month.weeks
  if (!Array.isArray(weeks)) {
    problems.push("weeks is not an array")
  } else {
    if (weeks.length < 28) {
      problems.push(`weeks has ${weeks.length} cells, expected at least 28`)
    }
    let expectedDay = 1
    let enabled = 0
    weeks.forEach((cell, index) => {
      if (!cell || typeof cell !== "object" || Array.isArray(cell)) {
        problems.push(`week cell ${index} is not an object`)
        return
      }
      if (typeof cell.holiday !== "boolean") problems.push(`week cell ${index} has no holiday flag`)
      if (typeof cell.disabled !== "boolean") problems.push(`week cell ${index} has no disabled flag`)
      const day = cell.day
      if (!day || typeof day !== "object" || Array.isArray(day)) {
        problems.push(`week cell ${index} has no day object`)
        return
      }
      for (const key of ["j", "m", "q"]) {
        if (!isNonEmptyString(day[key])) {
          problems.push(`week cell ${index} has an empty day.${key}`)
        }
      }
      if (cell.disabled === false) {
        const dayNumber = Number(day.j)
        if (!Number.isInteger(dayNumber) || String(dayNumber) !== day.j.trim()) {
          problems.push(`enabled week cell ${index} has a non-numeric day: ${JSON.stringify(day.j)}`)
        } else if (dayNumber !== expectedDay) {
          problems.push(`enabled week cell ${index} is day ${dayNumber}, expected ${expectedDay}`)
        }
        expectedDay += 1
        enabled += 1
      }
    })
    if (enabled < 29 || enabled > 31) {
      problems.push(`month has ${enabled} enabled day cells, expected 29 to 31`)
    }
  }

  const events = month.events
  if (!Array.isArray(events)) {
    problems.push("events is not an array")
  } else if (events.length === 0) {
    // time.ir always publishes events for every month; an empty list is the
    // typical symptom of a selector that no longer matches the page.
    problems.push("events is empty")
  } else {
    const monthPrefix = `${year}/${pad2(monthNumber)}/`
    events.forEach((event, index) => {
      if (!event || typeof event !== "object" || Array.isArray(event)) {
        problems.push(`event ${index} is not an object`)
        return
      }
      if (typeof event.isHoliday !== "boolean") problems.push(`event ${index} has no isHoliday flag`)
      if (!isNonEmptyString(event.text)) problems.push(`event ${index} has no text`)
      if (!isNonEmptyString(event.jDate) || !event.jDate.startsWith(monthPrefix)) {
        problems.push(
          `event ${index} has jDate ${JSON.stringify(event.jDate)}, expected ${monthPrefix}DD`,
        )
      } else if (
        !isNonEmptyString(event.jDay) ||
        Number(event.jDay) !== Number(event.jDate.slice(8, 10))
      ) {
        problems.push(
          `event ${index} has jDay ${JSON.stringify(event.jDay)} inconsistent with ${event.jDate}`,
        )
      }
      if (!isNonEmptyString(event.mDate) || !parseGregorian(event.mDate)) {
        problems.push(`event ${index} has an invalid Gregorian mDate: ${JSON.stringify(event.mDate)}`)
      }
    })
  }

  return problems
}

const monthOutcome = (monthNumber, fields) => ({
  month: monthNumber,
  status: fields.status,
  code: fields.code || null,
  label: fields.label || null,
  reason: fields.reason || null,
})

const successOutcome = (monthNumber, month) => ({
  ...monthOutcome(monthNumber, {
    status: STATUS.SUCCESS,
    label: isNonEmptyString(month.header && month.header.jalali) ? month.header.jalali : null,
  }),
  data: month,
})

const failedOutcome = (monthNumber, code, reason, label = null) =>
  monthOutcome(monthNumber, { status: STATUS.FAILED, code, label, reason })

const notAttemptedOutcome = (monthNumber, reason) =>
  monthOutcome(monthNumber, { status: STATUS.NOT_ATTEMPTED, code: "NOT_ATTEMPTED", reason })

const defaultNotAttemptedReason = (year, monthNumber) =>
  `Month ${monthNumber} of ${year} was not attempted`

// One extracted entry per month, in calendar order. A hole means extraction
// never reached that month. Accepted shapes:
//   { index, ok: true, data }          month successfully extracted
//   { index, ok: false, code, reason } page/extractor reported the month broken
//   { index, status: "not-attempted" } extraction never reached the month
//   month data object                  convenience for callers and fixtures
//   null / undefined / missing         not attempted
const outcomeForEntry = (year, monthNumber, entry, options) => {
  if (entry === undefined || entry === null) {
    return notAttemptedOutcome(
      monthNumber,
      options.notAttemptedReason || defaultNotAttemptedReason(year, monthNumber),
    )
  }

  if (entry.status === STATUS.NOT_ATTEMPTED) {
    return notAttemptedOutcome(
      monthNumber,
      entry.reason || options.notAttemptedReason || defaultNotAttemptedReason(year, monthNumber),
    )
  }

  if (entry.ok === false || entry.error) {
    const error = entry.error || {}
    return failedOutcome(
      monthNumber,
      entry.code || error.code || "MONTH_PARSE_ERROR",
      entry.reason || error.message || `Month ${monthNumber} of ${year} could not be parsed`,
    )
  }

  const data = entry.data || entry.month || (entry.header ? entry : null)
  if (!data) {
    return failedOutcome(
      monthNumber,
      "MONTH_PARSE_ERROR",
      `Month ${monthNumber} of ${year} returned no usable data`,
    )
  }

  const problems = validateMonthData(year, monthNumber, data)
  if (problems.length) {
    return failedOutcome(
      monthNumber,
      "MONTH_PARSE_ERROR",
      `Month ${monthNumber} of ${year} has an unexpected structure: ${problems
        .slice(0, 5)
        .join("; ")}`,
      isNonEmptyString(data.header && data.header.jalali) ? data.header.jalali : null,
    )
  }

  return successOutcome(monthNumber, data)
}

const collectMonths = (year, rawMonths, options = {}) => {
  const entries = Array.isArray(rawMonths) ? rawMonths : []
  return Array.from({ length: MONTH_COUNT }, (_, index) =>
    outcomeForEntry(year, index + 1, entries[index], options),
  )
}

// Extraction may return a bare 12-slot array or { months, panelErrors };
// panelErrors describe panels that matched no month and are reported as-is.
const normalizeExtraction = raw => {
  if (Array.isArray(raw)) return { months: raw, panelErrors: [] }
  if (raw && typeof raw === "object") {
    return {
      months: Array.isArray(raw.months) ? raw.months : [],
      panelErrors: Array.isArray(raw.panelErrors) ? raw.panelErrors : [],
    }
  }
  return { months: [], panelErrors: [] }
}

const missingMonthList = months =>
  months.filter(month => month.status !== STATUS.SUCCESS).map(month => month.month)

// Turns raw extraction output into a year-level report. Never drops a month:
// every month has an explicit outcome with a human-readable reason.
const finalizeYear = (year, rawMonths, options = {}) => {
  const { months: entries, panelErrors } = normalizeExtraction(rawMonths)
  const months = collectMonths(year, entries, options)
  const successful = months.filter(month => month.status === STATUS.SUCCESS)
  const failed = months.filter(month => month.status === STATUS.FAILED)
  const notAttempted = months.filter(month => month.status === STATUS.NOT_ATTEMPTED)

  let status
  if (successful.length === 0) status = "failed"
  else if (successful.length === MONTH_COUNT) status = "complete"
  else status = "partial"

  let reason = null
  if (status === "failed") {
    reason =
      notAttempted.length === MONTH_COUNT && options.notAttemptedReason
        ? options.notAttemptedReason
        : `No month of ${year} could be collected (${failed.length} failed, ${notAttempted.length} not attempted)`
  } else if (status === "partial") {
    reason = `Collected ${successful.length} of ${MONTH_COUNT} months for ${year}; unavailable months: ${missingMonthList(
      months,
    ).join(", ")}`
  }

  return {
    year: Number(year),
    status,
    reason,
    counts: {
      success: successful.length,
      failed: failed.length,
      notAttempted: notAttempted.length,
    },
    months: months.map(({ data, ...outcome }) => outcome),
    // Only genuinely collected months. Missing months are never represented by
    // an empty month object, so consumers can tell a gap from an empty month.
    collected: successful.map(month => month.data),
    failedMonths: failed.map(month => month.month),
    notAttemptedMonths: notAttempted.map(month => month.month),
    panelErrors,
  }
}

const writeJson = (file, value) => {
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, JSON.stringify(value))
}

const apiExportReason = (report, requested) => {
  if (!requested) return "API export is disabled"
  if (report.status === "failed") {
    return `No API files were written for ${report.year}: no month could be collected`
  }
  if (report.status === "partial") {
    return `events.json and weeks.json were not exported for ${
      report.year
    }: they require all ${MONTH_COUNT} months, but only ${
      report.counts.success
    } were collected (unavailable months: ${missingMonthList(report.months).join(", ")})`
  }
  return null
}

// Writes every per-year output. A failed year writes nothing at all: no calendar
// entry, no API directory, no outcome file. A partial year writes only the months
// that were actually collected and skips the flat derived endpoints.
const exportYear = (report, options = {}) => {
  const api = Boolean(options.api)
  const apiRoot = options.apiRoot || "./api"
  const distRoot = options.distRoot || "./dist"
  const written = { calendarEntry: false, outcome: false, index: false, events: false, weeks: false }
  const apiResult = {
    requested: api,
    exported: false,
    index: false,
    events: false,
    weeks: false,
    reason: apiExportReason(report, api),
  }

  if (report.status === "failed" || report.counts.success === 0) {
    return { written, api: apiResult, calendarEntry: null }
  }

  written.calendarEntry = true
  const calendarEntry = { [report.year]: report.collected }

  writeJson(path.join(distRoot, `outcome-${report.year}.json`), { ...report, api: apiResult })
  written.outcome = true

  if (api) {
    const directory = path.join(apiRoot, String(report.year))
    if (report.status === "complete") {
      createJsonApiFile(report.year, calendarEntry, apiRoot)
      written.index = true
      written.events = true
      written.weeks = true
      apiResult.exported = true
      apiResult.index = true
      apiResult.events = true
      apiResult.weeks = true
    } else {
      writeJson(path.join(directory, "index.json"), calendarEntry)
      written.index = true
      apiResult.exported = true
      apiResult.index = true
    }

    writeJson(path.join(directory, "outcome.json"), { ...report, api: apiResult })
  }

  return { written, api: apiResult, calendarEntry }
}

module.exports = {
  MONTH_COUNT,
  STATUS,
  validateMonthData,
  collectMonths,
  finalizeYear,
  exportYear,
}
