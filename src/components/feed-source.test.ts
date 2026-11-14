import assert from "node:assert/strict"
import { register } from "node:module"

const hook = `
export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith("@/")) {
    const target = new URL("../" + specifier.slice(2) + ".ts", ${JSON.stringify(import.meta.url)})
    return nextResolve(target.href, context)
  }
  const resolved = await nextResolve(specifier, context)
  if (resolved.url.endsWith(".json")) {
    resolved.importAttributes = { ...resolved.importAttributes, type: "json" }
  }
  return resolved
}
`
register(`data:text/javascript,${encodeURIComponent(hook)}`)

const { MESSAGES } = await import("../lib/i18n.ts")
const { feedSourceCopy } = await import("./map-cards.ts")

// feedSourceCopy is the pure mapping the stop popup uses to turn API provenance
// ("live" / "stale" / "missing") into a visible badge, so the three states can be
// asserted without a DOM or any fetch.
const en = MESSAGES.en
const hk = MESSAGES["zh-HK"]
const sc = MESSAGES["zh-CN"]

const fetchedAt = "2026-11-14T08:00:00.000Z"

assert.equal(feedSourceCopy("live", fetchedAt, en), en.feedLive)
assert.equal(feedSourceCopy("live", fetchedAt, hk), hk.feedLive)
assert.equal(feedSourceCopy("missing", null, en), en.feedMissing)
assert.equal(feedSourceCopy("missing", null, hk), hk.feedMissing)
assert.equal(feedSourceCopy("missing", null, sc), sc.feedMissing)

// A stale reading must carry the original fetch time in every locale.
const staleEn = feedSourceCopy("stale", fetchedAt, en)
assert.match(staleEn, /Cached times/)
assert.ok(staleEn.includes("14 Nov"))
const staleHk = feedSourceCopy("stale", fetchedAt, hk)
assert.match(staleHk, /快取/)
assert.ok(staleHk.includes("16:00"))
const staleSc = feedSourceCopy("stale", fetchedAt, sc)
assert.match(staleSc, /缓存/)
assert.ok(staleSc.includes("16:00"))

// A stale payload without a timestamp still renders, just without a time fragment.
const staleNoTime = feedSourceCopy("stale", null, en)
assert.match(staleNoTime, /Cached times/)
assert.ok(!staleNoTime.includes("Nov"))

// Defensive default: an absent source is treated as live.
assert.equal(feedSourceCopy(undefined, null, en), en.feedLive)

console.log("feed source copy ok")
