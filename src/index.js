const crawlTimeIr = require("./pipe2time.ir")
const buildICS = require("./pipe2calendar")

const inquirer = require("inquirer")
const questions = [
	{
		type: "input",
		name: "years",
		message: "Enter your needed years? (separate by ',')",
	},
	{
		type: "confirm",
		name: "ics",
		default: false,
		message: "Also, do you wanna export the calendar as .ics files?",
	},
	{
		type: "confirm",
		name: "api",
		default: false,
		message: "Do you wanna export the calendar as separated API files?",
	},
]

inquirer.prompt(questions).then(async ({ years, ics, api }) => {
	const yParse = years
		.split(",")
		.map(year => year.trim())
		.filter(Boolean)
	const invalid = yParse.filter(year => !(+year <= 1500 && +year >= 1280))
	if (invalid.length) {
		for (const year of invalid) {
			console.error(
				`Input year does not in range of support. year must grather than 1280 and less than 1500, input year => ${year}`,
			)
		}
		process.exitCode = 1
		return
	}

	const result = await crawlTimeIr(yParse, api)
	if (ics) {
		// Only years that actually produced data can produce an .ics file.
		const collected = new Set(
			result.years.filter(year => year.counts.success > 0).map(year => year.year),
		)
		const missing = yParse.filter(year => !collected.has(Number(year)))
		for (const year of missing) {
			const reason =
				result.failedYears.find(failed => failed.year === Number(year))?.reason ||
				`No calendar data was collected for ${year}`
			console.error(`Skipping event-${year}.ics: ${reason}`)
		}
		for (const year of collected) buildICS(year)
	}

	if (!result.ok) {
		console.error(result.reason)
		process.exitCode = 1
	}
})
