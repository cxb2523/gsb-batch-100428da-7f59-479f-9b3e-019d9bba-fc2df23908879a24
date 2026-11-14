const fs = require("node:fs")
const path = require("node:path")
const { finalizeYear, exportYear } = require("./year-collection")

const YEAR_URL =
	"https://www.time.ir/fa/eventyear-%D8%AA%D9%82%D9%88%DB%8C%D9%85-%D8%B3%D8%A7%D9%84%DB%8C%D8%A7%D9%86%D9%87"
const YEAR_INPUT =
	"#ctl00_cphTop_Sampa_Web_View_EventUI_EventYearCalendar10cphTop_3417_txtYear"

// Runs inside the browser page. Every month panel is extracted into its own
// slot so one changed panel cannot discard the other eleven. The result is
// always a 12-slot array; each slot is one of:
//   { index, ok: true, data }
//   { index, ok: false, code, reason }
//   { index, status: "not-attempted", reason }
// Month slots are addressed by the month named in each panel header, not by
// panel position, so a dropped panel becomes a reported gap instead of shifting
// the remaining months.
const extractMonthsInPage = requestedYear => {
	const persianNumbers = [/۰/g, /۱/g, /۲/g, /۳/g, /۴/g, /۵/g, /۶/g, /۷/g, /۸/g, /۹/g]
	const arabicNumbers = [/٠/g, /١/g, /٢/g, /٣/g, /٤/g, /٥/g, /٦/g, /٧/g, /٨/g, /٩/g]
	function toEnglishDigits(str) {
		if (typeof str === "string")
			for (let i = 0; i < 10; i++) {
				str = str.replace(persianNumbers[i], i).replace(arabicNumbers[i], i)
			}
		return str
	}

	// time.ir has used both spellings for month 5 across years, so each month
	// lists every header prefix that maps to it.
	const monthAliases = [
		["فروردین", "فرودین"],
		["اردیبهشت"],
		["خرداد"],
		["تیر"],
		["مرداد", "امرداد"],
		["شهریور"],
		["مهر"],
		["آبان"],
		["آذر", "اذر"],
		["دی"],
		["بهمن"],
		["اسفند"],
	]
	const normalizeHeader = text =>
		toEnglishDigits(String(text || ""))
			.replace(/[ً-ْٰـ]/g, "")
			.replace(/[0-9]/g, "")
			.replace(/\s+/g, " ")
			.trim()

	const monthNumberFromHeader = text => {
		const normalized = normalizeHeader(text)
		const index = monthAliases.findIndex(aliases =>
			aliases.some(alias => normalized.startsWith(alias)),
		)
		return index === -1 ? null : index + 1
	}

	const pageYear = toEnglishDigits(String($("input[type='text']").val() || "")).trim()
	const year = pageYear || String(requestedYear)

	const failure = (code, reason) => ({
		months: Array.from({ length: 12 }, (_, index) => ({ index, ok: false, code, reason })),
		panelErrors: [],
	})

	if (String(year) !== String(requestedYear)) {
		return failure(
			"YEAR_MISMATCH",
			`The page shows year ${pageYear || "(empty)"}, not the requested year ${requestedYear}`,
		)
	}

	const moment = window.moment
	if (typeof moment !== "function") {
		return failure(
			"EXTRACTOR_UNAVAILABLE",
			`The calendar helper loaded on the page exposes no moment() for year ${requestedYear}`,
		)
	}

	let panels
	try {
		panels = $(".panel-body")
	} catch (error) {
		return failure(
			"PAGE_STRUCTURE_ERROR",
			`Could not query month panels for ${requestedYear}: ${
				error && error.message ? error.message : error
			}`,
		)
	}

	const extractMonth = (panel, monthNumber) => {
		const $this = panel
		const month = { header: {}, events: [], weeks: [] }

		$this.find(".header .miladi").each(function () {
			month.header.miladi = $(this).text()
		})
		$this.find(".header .qamari").each(function () {
			month.header.qamari = toEnglishDigits($(this).text())
		})
		$this.find(".header .jalali").each(function () {
			month.header.jalali = toEnglishDigits($(this).text())
		})

		$this.find(".dayList > div").each(function () {
			const jj = toEnglishDigits($(this).find(".jalali").text())
			const mi = $(this).find(".miladi").text()
			const qa = toEnglishDigits($(this).find(".qamari").text())
			const disable = $(this).hasClass("disabled")
			const holiday = $(this).find("div").hasClass(" holiday")
			month.weeks.push({
				holiday: holiday,
				disabled: disable,
				day: { j: jj, m: mi, q: qa },
			})
		})

		$this.find(".list-unstyled li").each(function () {
			const isHoliday = $(this).hasClass("eventHoliday")
			const text = toEnglishDigits(
				$(this).text().trim().replace(/(?:\r|\n)/g, " ").replace(/\s{2}/g, ""),
			)
			const day = toEnglishDigits(text.slice(0, 2).trim())
			const date = `${year}/${monthNumber}/${day}`
			const jDate = moment(date, "jYYYY/jM/jD").format("jYYYY/jMM/jDD")
			const mDate = moment(date, "jYYYY/jM/jD").format("YYYY/MM/DD")
			month.events.push({ isHoliday, text, jDate, mDate, jDay: day })
		})

		return month
	}

	// Start from "no panel for this month"; identified panels then claim their
	// own slot, and anything left over is reported as missing. Panel-level
	// anomalies (duplicate or unnamed panels) are returned separately: they do
	// not belong to any single month, so they must not masquerade as one.
	const entries = Array.from({ length: 12 }, (_, index) => ({
		index,
		ok: false,
		code: "MONTH_PANEL_MISSING",
		reason: `Month ${index + 1} of ${requestedYear} has no calendar panel on the page`,
	}))
	const panelErrors = []
	const failuresBySlot = {}

	panels.each(function (panelIndex) {
		let monthNumber
		try {
			const header = $(this).find(".header .jalali").first().text()
			monthNumber = monthNumberFromHeader(header)
		} catch (error) {
			panelErrors.push({
				index: panelIndex,
				code: "MONTH_PARSE_ERROR",
				reason: `Panel ${panelIndex + 1} header could not be read: ${
					error && error.message ? error.message : String(error)
				}`,
			})
			return
		}

		if (!monthNumber) {
			panelErrors.push({
				index: panelIndex,
				code: "MONTH_UNIDENTIFIED",
				reason: `Panel ${panelIndex + 1} does not name one of the twelve months`,
			})
			return
		}

		const slot = monthNumber - 1
		if (entries[slot].ok === true || failuresBySlot[slot]) {
			panelErrors.push({
				index: panelIndex,
				code: "MONTH_DUPLICATED",
				reason: `Panel ${panelIndex + 1} repeats month ${monthNumber}, which another panel already provided`,
			})
			return
		}

		try {
			entries[slot] = { index: slot, ok: true, data: extractMonth($(this), monthNumber) }
		} catch (error) {
			failuresBySlot[slot] = {
				index: slot,
				ok: false,
				code: "MONTH_PARSE_ERROR",
				reason: `Month ${monthNumber} could not be parsed: ${
					error && error.message ? error.message : String(error)
				}`,
			}
		}
	})

	for (const [slot, failure] of Object.entries(failuresBySlot)) {
		entries[Number(slot)] = failure
	}

	return { months: entries, panelErrors }
}

const openYearPage = async (browser, year) => {
	const page = await browser.newPage()
	await page.setDefaultNavigationTimeout(0)
	await page.goto(YEAR_URL, { waitUntil: "load" })
	await page.waitFor(YEAR_INPUT)
	await page.$eval(YEAR_INPUT, el => (el.value = ""))
	await page.type(YEAR_INPUT, year + "")
	await page.keyboard.press("Enter")
	await page.waitForNavigation()
	await page.addScriptTag({
		path: "./node_modules/jalali-moment/dist/jalali-moment.browser.js",
	})
	return page
}

// Extracts one year as 12 independent month outcomes. A page-level failure
// (navigation, browser, injected script) returns no entries at all, which the
// collector reports as "not attempted" rather than blaming the months.
const extractYear = async (browser, year) => {
	const page = await openYearPage(browser, year)
	try {
		return await page.evaluate(extractMonthsInPage, Number(year))
	} finally {
		await page.close()
	}
}

const defaultLaunchBrowser = () => {
	const puppeteer = require("puppeteer")
	return puppeteer.launch({ headless: true })
}

const yearList = years => {
	if (typeof years === "string") {
		return years
			.split(",")
			.map(year => year.trim())
			.filter(Boolean)
	}
	if (typeof years === "number") return [String(years)]
	return Array.isArray(years) ? years.map(String) : []
}

const summarize = reports => {
	const succeeded = reports.filter(report => report.counts.success > 0)
	if (!succeeded.length) return "failed"
	if (succeeded.length === reports.length && reports.every(report => report.status === "complete")) {
		return "complete"
	}
	return "partial"
}

const logReport = report => {
	for (const month of report.months) {
		const slot = `${report.year}/${String(month.month).padStart(2, "0")}`
		if (month.status === "success") {
			console.info(`  ${slot} ok${month.label ? ` (${month.label})` : ""}`)
		} else {
			console.warn(`  ${slot} ${month.status} [${month.code}]: ${month.reason}`)
		}
	}
}

// Crawl entry point. Keeps the original signature: years plus the API-export
// switch. Returns a structured result so callers can distinguish a complete
// year, a partial year, and a total failure instead of a silent success.
// Browser launch and extraction are injectable so orchestration can be tested
// offline; the defaults never touch the network until called.
const init = async (years, activeApi, options = {}) => {
	const requestedYears = yearList(years)
	if (!requestedYears.length) {
		return {
			ok: false,
			status: "failed",
			reason: "No years were requested",
			years: [],
			failedYears: [],
			calendarPath: null,
		}
	}

	const launchBrowser = options.launchBrowser || defaultLaunchBrowser
	const extract = options.extractYear || extractYear
	const distRoot = options.distRoot || "./dist"
	const apiRoot = options.apiRoot || "./api"

	const reports = []
	let browser = null
	let launchError = null

	try {
		browser = await launchBrowser()
	} catch (error) {
		launchError = error
	}

	try {
		for (const year of requestedYears) {
			if (launchError) {
				const message = `Year ${year} was not attempted: the browser could not start (${launchError.message})`
				console.error(message)
				const report = finalizeYear(year, [], { notAttemptedReason: message })
				const exported = exportYear(report, {
					api: Boolean(activeApi),
					apiRoot,
					distRoot,
				})
				reports.push({ ...report, api: exported.api, written: exported.written })
				continue
			}

			console.info(`Getting ${year} info ...`)
			let entries = []
			let extractionError = null
			try {
				entries = await extract(browser, year)
			} catch (error) {
				extractionError = error
			}

			const report = finalizeYear(
				year,
				extractionError ? [] : entries,
				extractionError
					? {
							notAttemptedReason: `Year ${year} was not attempted: extraction stopped before any month was read (${extractionError.message})`,
					  }
					: {},
			)
			logReport(report)

			const exported = exportYear(report, {
				api: Boolean(activeApi),
				apiRoot,
				distRoot,
			})

			if (report.counts.success > 0) {
				if (exported.api.reason) console.info(`  ${report.year}: ${exported.api.reason}`)
				console.info(
					`The ${year} info extracted (${report.status}, ${report.counts.success}/12 months).`,
				)
			} else {
				console.error(`${year}: no month could be collected; no output files written.`)
			}

			reports.push({ ...report, api: exported.api, written: exported.written })
		}
	} finally {
		if (browser && typeof browser.close === "function") {
			await browser
				.close()
				.catch(error => console.error(`Could not close the browser: ${error.message}`))
		}
	}

	const calendar = {}
	for (const report of reports) {
		if (report.counts.success > 0) calendar[report.year] = report.collected
	}

	let calendarPath = null
	if (Object.keys(calendar).length) {
		calendarPath = path.join(distRoot, "calendar.json")
		fs.mkdirSync(distRoot, { recursive: true })
		fs.writeFileSync(calendarPath, JSON.stringify(calendar))
	}

	const status = summarize(reports)
	const failedYears = reports
		.filter(report => report.counts.success === 0)
		.map(report => ({ year: report.year, reason: report.reason, months: report.months }))

	const result = {
		ok: status !== "failed",
		status,
		calendarPath,
		years: reports.map(report => ({
			year: report.year,
			status: report.status,
			reason: report.reason,
			counts: report.counts,
			months: report.months,
			failedMonths: report.failedMonths,
			notAttemptedMonths: report.notAttemptedMonths,
			api: report.api,
			files: report.written,
		})),
		failedYears,
	}

	if (status === "failed") {
		result.reason = `No year could be collected; no output files were written (${requestedYears.join(
			", ",
		)})`
		console.error(result.reason)
	} else {
		console.info(`Don. calendar written to ${calendarPath}`)
	}

	return result
}

module.exports = init
module.exports.extractMonthsInPage = extractMonthsInPage
module.exports.extractYear = extractYear
