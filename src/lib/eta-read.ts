import { ETA_FRESH_MS } from "@/lib/place-arrivals"
import { etaQueue } from "@/lib/polite-fetch"
import { fetchUpstream, type UpstreamBody } from "@/lib/upstream"
import type { FeedSource } from "@/lib/types"

const ETA_HEADERS = {
  Accept: "application/json",
  "User-Agent": "Mozilla/5.0 (compatible; HKTrafficIntelligence/1.0; +https://hktraffic.keith-li.workers.dev)",
}

export type EtaRead<T> = {
  // Parsed JSON of the last good response. Null only when no usable data exists.
  data: T | null
  source: FeedSource
  // Original upstream fetch time (ISO 8601) of the underlying response; null when missing.
  fetchedAt: string | null
}

// Result of reading one pole's ETA rows: null rows mean the upstream was unreachable,
// while source/fetchedAt describe where the rows came from for board-level provenance.
export type PoleRead<Row> = {
  rows: Row[] | null
  source: FeedSource
  fetchedAt: string | null
}

export async function readEtaFeed<T>(url: string, blank: T | null = null): Promise<EtaRead<T>> {
  let response: UpstreamBody
  try {
    response = await etaQueue(() => fetchUpstream(url, ETA_FRESH_MS, {
      timeoutMs: 5_000,
      headers: ETA_HEADERS,
    }))
  } catch {
    return { data: null, source: "missing", fetchedAt: null }
  }
  if (response.status !== 200) return { data: null, source: "missing", fetchedAt: null }
  const text = new TextDecoder().decode(response.body)
  if (!text) return { data: blank, source: response.source, fetchedAt: stamp(response.fetchedAt) }
  return {
    data: JSON.parse(text) as T,
    source: response.source,
    fetchedAt: stamp(response.fetchedAt),
  }
}

function stamp(fetchedAt: number | null): string | null {
  return fetchedAt == null ? null : new Date(fetchedAt).toISOString()
}
