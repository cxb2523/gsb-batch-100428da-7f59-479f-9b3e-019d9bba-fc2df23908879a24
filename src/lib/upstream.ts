import { openFeedCache } from "./feed-cache.ts"
import type { FeedSource } from "./types.ts"

export type UpstreamBody = {
  status: number
  body: ArrayBuffer
  contentType: string
  // "live": a fresh success from the network or a still-valid cache entry.
  // "stale": an out-of-TTL success reused because the upstream failed.
  // "missing": no usable data; status carries the upstream failure (or the call throws on a timeout).
  source: FeedSource
  // Original fetch time of the underlying successful response (epoch ms).
  fetchedAt: number | null
}

type UpstreamOptions = {
  headers?: HeadersInit
  timeoutMs?: number
  // How long a successful response may be reused after its TTL while the upstream is failing.
  maxStaleMs?: number
}

type MemoryEntry = { expires: number; fetchedAt: number; body: UpstreamBody }

// Default ceiling for stale fallback: 10 minutes keeps a stalled feed useful without
// presenting hours-old departures as real-time information.
export const DEFAULT_MAX_STALE_MS = 10 * 60_000

const FETCHED_HEADER = "x-feed-fetched-at"

const memory = new Map<string, MemoryEntry>()
const pending = new Map<string, Promise<UpstreamBody>>()

export async function fetchUpstream(
  url: string,
  ttlMs: number,
  options: UpstreamOptions = {},
): Promise<UpstreamBody> {
  const fresh = memory.get(url)
  if (fresh && fresh.expires > Date.now()) return withSource(fresh.body, "live")
  const current = pending.get(url)
  if (current) return current
  const task = readThrough(url, ttlMs, options).finally(() => pending.delete(url))
  pending.set(url, task)
  return task
}

function withSource(body: UpstreamBody, source: FeedSource): UpstreamBody {
  return body.source === source ? body : { ...body, source }
}

async function readThrough(url: string, ttlMs: number, options: UpstreamOptions): Promise<UpstreamBody> {
  // A still-valid shared entry is served directly; it is deliberately NOT copied into
  // memory, so a later upstream failure can fall back to it rather than being masked.
  const shared = await readFreshShared(url, ttlMs)
  if (shared) return withSource(shared, "live")

  const held = memory.get(url) ?? (await readStaleShared(url))

  let response: Response
  try {
    response = await rawFetch()(url, {
      signal: AbortSignal.timeout(options.timeoutMs ?? 25_000),
      headers: options.headers,
    })
  } catch (error) {
    const fallback = staleOr(held, options.maxStaleMs)
    if (fallback) return fallback
    throw error
  }

  const contentType = response.headers.get("content-type") ?? ""
  const bytes = await response.arrayBuffer()
  if (response.ok) {
    const fetchedAt = Date.now()
    const body: UpstreamBody = { status: response.status, body: bytes, contentType, source: "live", fetchedAt }
    memory.set(url, { expires: fetchedAt + ttlMs, fetchedAt, body })
    await writeShared(url, ttlMs, body)
    return body
  }

  // Failures are never written to either cache. Reuse a previous success while it is
  // still within the stale window; otherwise hand the failure back to the caller.
  const fallback = staleOr(held, options.maxStaleMs)
  if (fallback) return fallback
  return { status: response.status, body: bytes, contentType, source: "missing", fetchedAt: null }
}

function staleOr(held: MemoryEntry | null, maxStaleMs = DEFAULT_MAX_STALE_MS): UpstreamBody | null {
  if (!held) return null
  const ceiling = maxStaleMs ?? DEFAULT_MAX_STALE_MS
  if (Date.now() - held.fetchedAt > ceiling) return null
  return withSource(held.body, "stale")
}

async function readFreshShared(url: string, ttlMs: number): Promise<UpstreamBody | null> {
  const cached = await matchShared(url)
  if (!cached) return null
  const fetchedAt = Number(cached.headers.get(FETCHED_HEADER))
  if (!Number.isFinite(fetchedAt)) return null
  // Entries past their TTL are stale-fallback material, not fresh reads.
  if (Date.now() - fetchedAt >= ttlMs) return null
  return {
    status: cached.status,
    body: await cached.arrayBuffer(),
    contentType: cached.headers.get("content-type") ?? "",
    source: "live",
    fetchedAt,
  }
}

async function readStaleShared(url: string): Promise<MemoryEntry | null> {
  const cached = await matchShared(url)
  if (!cached) return null
  const fetchedAt = Number(cached.headers.get(FETCHED_HEADER))
  if (!Number.isFinite(fetchedAt)) return null
  const body: UpstreamBody = {
    status: cached.status,
    body: await cached.arrayBuffer(),
    contentType: cached.headers.get("content-type") ?? "",
    source: "stale",
    fetchedAt,
  }
  const entry: MemoryEntry = { expires: fetchedAt, fetchedAt, body }
  memory.set(url, entry)
  return entry
}

async function matchShared(url: string): Promise<Response | null> {
  const cache = await openFeedCache()
  if (!cache) return null
  try {
    const cached = await cache.match(new Request(url))
    if (!cached?.ok) return null
    return cached
  } catch {
    return null
  }
}

async function writeShared(url: string, ttlMs: number, body: UpstreamBody): Promise<void> {
  const cache = await openFeedCache()
  if (!cache) return
  const seconds = Math.max(1, Math.round(ttlMs / 1000))
  try {
    await cache.put(
      new Request(url),
      new Response(body.body.slice(0), {
        status: 200,
        headers: {
          "Content-Type": body.contentType,
          "Cache-Control": `public, max-age=${seconds}`,
          [FETCHED_HEADER]: String(body.fetchedAt ?? Date.now()),
        },
      }),
    )
  } catch {
    // A rejected write must not fail the feed. The caller already has the body.
  }
}

function rawFetch(): typeof fetch {
  const saved = (globalThis as unknown as Record<symbol, typeof fetch | undefined>)[
    Symbol.for("vinext.fetchCache.originalFetch")
  ]
  return typeof saved === "function" ? saved : globalThis.fetch
}
