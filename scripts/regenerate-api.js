// Derive endpoints from committed source snapshots; never scrape or rewrite them.
const fs = require("node:fs")
const path = require("node:path")
const { yearOutputs } = require("../src/year-api")
const root = path.resolve(__dirname, "../api")
let incomplete = 0
for (const year of fs.readdirSync(root).filter(year => /^\d{4}$/.test(year)).sort()) {
  const directory = path.join(root, year)
  const data = JSON.parse(fs.readFileSync(path.join(directory, "index.json"), "utf8"))
  const months = data[year]
  if (!Array.isArray(months) || months.length !== 12) {
    // A partial scrape writes only the months it collected and deliberately
    // omits the flat endpoints, so there is nothing to derive here.
    incomplete++
    console.warn(
      `${year}: skipped, only ${Array.isArray(months) ? months.length : 0} of 12 months present; events.json and weeks.json require a complete year`,
    )
    continue
  }
  const outputs = yearOutputs(year, data)
  for (const [name, values] of Object.entries(outputs)) {
    fs.writeFileSync(path.join(directory, `${name}.json`), JSON.stringify(values))
  }
  console.log(`${year}: ${outputs.events.length} events, ${outputs.weeks.length} grid cells`)
}
if (incomplete) console.warn(`${incomplete} incomplete year(s) left unchanged`)
