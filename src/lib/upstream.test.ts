import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { register } from "node:module"

const hook = `
export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith("@/")) {
    const target = new URL("../" + specifier.slice(2) + ".ts", ${JSON.stringify(import.meta.url)})
    return nextResolve(target.href, context)
  }
  return nextResolve(specifier, context)
}
`
register(`data:text/javascript,${encodeURIComponent(hook)}`)

const { fetchUpstream, DEFAULT_MAX_STALE_MS } = await import("./upstream.ts")

type Stored = { body: string; contentType: string; fetchedAt: number; ttlSeconds: number }

// In-memory CacheStorage fake. Entries remember their own fetchedAt timestamp and TTL,
// so stale and over-age scenarios run without mocking the clock.
class FakeCache {
  readonly store = new Map<string, Stored>()
  puts = 0

  async match(request: RequestInfo | URL): Promise<Response | undefined> {
    const stored = this.store.get(keyOf(request))
    if (!stored) return undefined
    return new Response(stored.body, {
      status: 200,
      headers: {
        "content-type": stored.contentType,
        "cache-control": `public, max-age=${stored.ttlSeconds}`,
        "x-feed-fetched-at": String(stored.fetchedAt),
      },
    })
  }

  async put(request: RequestInfo | URL, response: Response): Promise<void> {
    this.puts += 1
    const fetchedAt = Number(response.headers.get("x-feed-fetched-at"))
    const cacheControl = response.headers.get("cache-control") ?? ""
    const ttlSeconds = Number(cacheControl.match(/max-age=(\d+)/)?.[1] ?? 0)
    this.store.set(keyOf(request), {
      body: await response.text(),
      contentType: response.headers.get("content-type") ?? "",
      fetchedAt,
      ttlSeconds,
    })
  }

  async delete(request: RequestInfo | URL): Promise<boolean> {
    return this.store.delete(keyOf(request))
  }
}

function keyOf(request: RequestInfo | URL): string {
  return typeof request === "object" && request !== null && "url" in request
    ? (request as Request).url
    : String(request)
}

const cache = new FakeCache()
globalThis.caches = {
  default: cache,
  async open() {
    return cache
  },
  async has() {
    return true
  },
  async keys() {
    return [] as string[]
  },
  async delete() {
    return true
  },
} as unknown as CacheStorage

type Behaviour = "ok" | "down" | "boom"
type FakeFetch = ((input: RequestInfo | URL, init?: RequestInit) => Promise<Response>) & {
  calls: string[]
}

function makeFetch(mode: (url: string) => Behaviour): FakeFetch {
  const fake = (async (input: RequestInfo | URL) => {
    const url = String(input)
    fake.calls.push(url)
    const behaviour = mode(url)
    if (behaviour === "boom") {
      await new Promise((resolve) => setTimeout(resolve, 5))
      throw new TypeError("network timeout")
    }
    return new Response(JSON.stringify({ from: behaviour === "ok" ? "network" : "upstream-error" }), {
      status: behaviour === "ok" ? 200 : 503,
      headers: { "content-type": "application/json" },
    })
  }) as FakeFetch
  fake.calls = []
  return fake
}

function installFetch(fake: FakeFetch): void {
  globalThis.fetch = fake as unknown as typeof fetch
}

function textOf(body: ArrayBuffer): string {
  return new TextDecoder().decode(body)
}

const TTL = 60_000
const networkUrl = "https://data.etabus.gov.hk/v1/transport/kmb/stop-eta/live"

// 1. A fresh success is marked "live", written once, and served from memory afterwards.
const liveFetch = makeFetch(() => "ok")
installFetch(liveFetch)
const first = await fetchUpstream(networkUrl, TTL)
assert.equal(first.source, "live")
assert.equal(first.status, 200)
assert.equal(textOf(first.body).includes("network"), true)
assert.equal(typeof first.fetchedAt, "number")
assert.equal(cache.puts, 1)
await fetchUpstream(networkUrl, TTL)
assert.equal(liveFetch.calls.length, 1)

// 2. A still-valid shared-cache entry is a "live" read with no upstream call.
const cachedUrl = "https://data.etabus.gov.hk/v1/transport/kmb/stop-eta/cached"
cache.store.set(cachedUrl, {
  body: JSON.stringify({ from: "cache" }),
  contentType: "application/json",
  fetchedAt: Date.now(),
  ttlSeconds: TTL / 1000,
})
const cached = await fetchUpstream(cachedUrl, TTL)
assert.equal(cached.source, "live")
assert.equal(textOf(cached.body).includes("cache"), true)
assert.equal(liveFetch.calls.some((url) => url === cachedUrl), false)

// 3. Non-200 upstream with no held data is a "missing" failure and writes nothing.
const downUrl = "https://data.etabus.gov.hk/v1/transport/kmb/stop-eta/down"
const downFetch = makeFetch(() => "down")
installFetch(downFetch)
const putsBeforeDown = cache.puts
const down = await fetchUpstream(downUrl, TTL)
assert.equal(down.source, "missing")
assert.equal(down.status, 503)
assert.equal(down.fetchedAt, null)
assert.equal(downFetch.calls.length, 1)
assert.equal(cache.puts, putsBeforeDown)

// 4. Upstream non-200 falls back to an out-of-TTL shared entry within the stale window.
const staleUrl = "https://data.etabus.gov.hk/v1/transport/kmb/stop-eta/stale-fallback"
const staleFetchedAt = Date.now() - (TTL + 5_000)
cache.store.set(staleUrl, {
  body: JSON.stringify({ from: "held" }),
  contentType: "application/json",
  fetchedAt: staleFetchedAt,
  ttlSeconds: TTL / 1000,
})
const staleFetch = makeFetch(() => "down")
installFetch(staleFetch)
const putsBeforeStale = cache.puts
const stale = await fetchUpstream(staleUrl, TTL, { maxStaleMs: 10 * 60_000 })
assert.equal(stale.source, "stale")
assert.equal(stale.status, 200)
assert.equal(textOf(stale.body).includes("held"), true)
assert.equal(stale.fetchedAt, staleFetchedAt)
// The failed response must not overwrite the cached success.
assert.equal(cache.puts, putsBeforeStale)

// 5. Stale fallback has a ceiling: entries older than maxStale are discarded.
const agedUrl = "https://data.etabus.gov.hk/v1/transport/kmb/stop-eta/aged"
cache.store.set(agedUrl, {
  body: JSON.stringify({ from: "old" }),
  contentType: "application/json",
  fetchedAt: Date.now() - (DEFAULT_MAX_STALE_MS + 60_000),
  ttlSeconds: TTL / 1000,
})
const agedFetch = makeFetch(() => "down")
installFetch(agedFetch)
const aged = await fetchUpstream(agedUrl, TTL)
assert.equal(aged.source, "missing")
assert.equal(aged.status, 503)
assert.equal(textOf(aged.body).includes("upstream-error"), true)
assert.equal(cache.store.get(agedUrl)?.body.includes("old"), true)

// 6. A thrown fetch (timeout) with an in-window stale body also falls back.
const timeoutUrl = "https://data.etabus.gov.hk/v1/transport/kmb/stop-eta/timeout"
const timeoutFetchedAt = Date.now() - (TTL + 15_000)
cache.store.set(timeoutUrl, {
  body: JSON.stringify({ from: "held" }),
  contentType: "application/json",
  fetchedAt: timeoutFetchedAt,
  ttlSeconds: TTL / 1000,
})
const boomFetch = makeFetch(() => "boom")
installFetch(boomFetch)
const recovered = await fetchUpstream(timeoutUrl, TTL)
assert.equal(recovered.source, "stale")
assert.equal(recovered.fetchedAt, timeoutFetchedAt)
assert.equal(textOf(recovered.body).includes("held"), true)

// 7. A thrown fetch with nothing usable propagates the error to the caller.
const bareBoom = makeFetch(() => "boom")
installFetch(bareBoom)
const bareUrl = "https://data.etabus.gov.hk/v1/transport/kmb/stop-eta/bare"
await assert.rejects(() => fetchUpstream(bareUrl, TTL), /network timeout/)

// 8. Concurrent calls to one URL collapse into a single upstream fetch and share the body.
const concurrentUrl = "https://data.etabus.gov.hk/v1/transport/kmb/stop-eta/concurrent"
let started = 0
let release: () => void = () => {}
const gate = new Promise<void>((resolve) => {
  release = resolve
})
globalThis.fetch = (async () => {
  started += 1
  await gate
  return new Response(JSON.stringify({ from: "network" }), {
    status: 200,
    headers: { "content-type": "application/json" },
  })
}) as unknown as typeof fetch
const joinedPromise = Promise.all([
  fetchUpstream(concurrentUrl, TTL),
  fetchUpstream(concurrentUrl, TTL),
  fetchUpstream(concurrentUrl, TTL),
])
// Wait until the single upstream call has started and all three callers are merged.
await new Promise((resolve) => setTimeout(resolve, 30))
assert.equal(started, 1)
release()
const joined = await joinedPromise
assert.deepEqual(joined.map((item) => item.source), ["live", "live", "live"])
assert.equal(new Set(joined.map((item) => textOf(item.body))).size, 1)

// 9. A failed request is not cached and does not poison the next attempt.
const flakyUrl = "https://data.etabus.gov.hk/v1/transport/kmb/stop-eta/flaky"
let attempts = 0
globalThis.fetch = (async () => {
  attempts += 1
  if (attempts === 1) {
    return new Response("busy", { status: 502, headers: { "content-type": "text/plain" } })
  }
  return new Response(JSON.stringify({ from: "recovered" }), {
    status: 200,
    headers: { "content-type": "application/json" },
  })
}) as unknown as typeof fetch
const flakyFirst = await fetchUpstream(flakyUrl, TTL)
assert.equal(flakyFirst.source, "missing")
const flakySecond = await fetchUpstream(flakyUrl, TTL)
assert.equal(flakySecond.source, "live")
assert.equal(textOf(flakySecond.body).includes("recovered"), true)
assert.equal(attempts, 2)

// 10. A custom maxStaleMs narrows the fallback window independently of the default.
const narrowOverAgeUrl = "https://data.etabus.gov.hk/v1/transport/kmb/stop-eta/narrow-overage"
cache.store.set(narrowOverAgeUrl, {
  body: JSON.stringify({ from: "older" }),
  contentType: "application/json",
  fetchedAt: Date.now() - (3 * 60_000),
  ttlSeconds: TTL / 1000,
})
installFetch(makeFetch(() => "down"))
const narrow = await fetchUpstream(narrowOverAgeUrl, TTL, { maxStaleMs: 60_000 })
assert.equal(narrow.source, "missing")

const narrowInWindowUrl = "https://data.etabus.gov.hk/v1/transport/kmb/stop-eta/narrow-in-window"
cache.store.set(narrowInWindowUrl, {
  body: JSON.stringify({ from: "older" }),
  contentType: "application/json",
  fetchedAt: Date.now() - (TTL + 5_000),
  ttlSeconds: TTL / 1000,
})
installFetch(makeFetch(() => "down"))
const within = await fetchUpstream(narrowInWindowUrl, TTL, { maxStaleMs: 2 * 60_000 })
assert.equal(within.source, "stale")

const memory = readFileSync(new URL("./mtr-feed.ts", import.meta.url), "utf8")
assert.match(memory, /const MEMORY_URL = "https:\/\/hktraffic-cache\.invalid\/mtr-board-memory"/)
assert.equal(memory.includes("https://hktraffic.keith-li.workers.dev/internal/"), false)

console.log("upstream cache ok")
