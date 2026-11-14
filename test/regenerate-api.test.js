const { test } = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")
const { spawnSync } = require("node:child_process")

// Runs the real regenerate script against a throwaway api/ root by pointing it
// at a copy of the repo layout. No network or browser involved.
test("regenerate-api skips an incomplete year instead of failing the whole run", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "regen-api-"))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))

  const scriptDir = path.join(root, "scripts")
  const srcDir = path.join(root, "src")
  const apiDir = path.join(root, "api")
  fs.mkdirSync(scriptDir, { recursive: true })
  fs.mkdirSync(srcDir, { recursive: true })
  fs.mkdirSync(path.join(apiDir, "1405"), { recursive: true })
  fs.mkdirSync(path.join(apiDir, "1406"), { recursive: true })

  fs.copyFileSync(path.resolve(__dirname, "../src/year-api.js"), path.join(srcDir, "year-api.js"))
  fs.copyFileSync(path.resolve(__dirname, "../scripts/regenerate-api.js"), path.join(scriptDir, "regenerate-api.js"))

  const month = {
    header: { miladi: "x", qamari: "x", jalali: "x" },
    events: [{ text: "e" }],
    weeks: [{ disabled: true }],
  }
  // 1405 is complete; 1406 is a partial scrape with only 9 months.
  fs.writeFileSync(path.join(apiDir, "1405/index.json"), JSON.stringify({ 1405: Array(12).fill(month) }))
  fs.writeFileSync(path.join(apiDir, "1406/index.json"), JSON.stringify({ 1406: Array(9).fill(month) }))

  const run = spawnSync(process.execPath, [path.join(scriptDir, "regenerate-api.js")], {
    encoding: "utf8",
    // Keep the script's own require of ../src/year-api resolving inside the copy.
    cwd: root,
  })
  assert.equal(run.status, 0, run.stderr)
  const output = run.stdout
  const stderr = run.stderr

  assert.match(output, /1405: 12 events/)
  assert.match(stderr, /1406: skipped, only 9 of 12 months present/)
  assert.equal(fs.existsSync(path.join(apiDir, "1405/events.json")), true)
  assert.equal(fs.existsSync(path.join(apiDir, "1406/events.json")), false)
  assert.deepEqual(
    JSON.parse(fs.readFileSync(path.join(apiDir, "1406/index.json"), "utf8"))[1406].length,
    9,
    "the partial source index is left untouched",
  )
})
