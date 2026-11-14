// ABOUTME: Minimal SSRF guard for discover-apply's soft evidence re-fetch. The RESEARCH
// ABOUTME: fetch is already guarded by Atlas's built-in safety.js (we pass
// ABOUTME: safety:{allowPrivateNetworks:false} to new Atlas()). The APPLY step re-fetches a
// ABOUTME: candidate's sourceUrl *independently* to confirm the score number is really there,
// ABOUTME: so it must enforce the same host/IP rules itself. These rules mirror Atlas's
// ABOUTME: isPrivateIPv4Octets + scheme restriction + DNS-resolution check.

import dns from "node:dns/promises";
import net from "node:net";
import type { LookupAddress } from "node:dns";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { tmpdir } from "node:os";
import { writeFile, unlink } from "node:fs/promises";
import path from "node:path";

const execFileP = promisify(execFile);

// Upper bound on a fetched body's size (bytes). Guards the soft evidence re-fetch against a
// multi-GB response OOMing the process before pdftotext/UTF-8 decoding can run. Enforced BOTH
// against Content-Length (cheap pre-check) AND while the body is being consumed: a chunked/streamed
// response without Content-Length is counted as it is read and aborted the moment the running total
// crosses the cap, so a hostile page can never be fully buffered first.
const MAX_FETCH_BYTES = 16 * 1024 * 1024; // 16 MiB
// Redirect-chain diagnostics: reasons are surfaced in PR bodies / verify JSON, so keep the URLs short.
const MAX_URL_IN_REASON = 160;

function shortUrl(u: string): string {
  return u.length > MAX_URL_IN_REASON ? `${u.slice(0, MAX_URL_IN_REASON - 3)}...` : u;
}

export type SsrfVerdict = { ok: true } | { ok: false; reason: string };

// URL-level checks (no DNS): scheme must be http/https, no embedded credentials.
export function assertSafePublicUrl(rawUrl: string): SsrfVerdict {
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    return { ok: false, reason: "not a valid URL" };
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return { ok: false, reason: `scheme ${url.protocol} not allowed (http/https only)` };
  }
  if (url.username || url.password) {
    return { ok: false, reason: "URL must not contain credentials" };
  }
  return { ok: true };
}

// Resolve a hostname and reject if ANY resolved address is private/loopback/link-local/metadata.
// For a literal IP, check it directly. This is the per-hop guard run before every fetch.
export async function assertSafeHost(host: string): Promise<SsrfVerdict> {
  if (net.isIP(host)) return checkIp(host);
  let addrs: LookupAddress[];
  try {
    addrs = await dns.lookup(host, { all: true });
  } catch (e) {
    return { ok: false, reason: `DNS lookup failed: ${(e as Error).message}` };
  }
  if (addrs.length === 0) return { ok: false, reason: "DNS returned no addresses" };
  for (const a of addrs) {
    const v = checkIp(a.address);
    if (!v.ok) return { ok: false, reason: `${host} -> ${a.address}: ${v.reason}` };
  }
  return { ok: true };
}

export type SafeFetchResult =
  | { ok: true; status: number; text: string; contentType: string; bytes: Uint8Array }
  | { ok: false; reason: string };

// Read a response body with a hard streaming cap. Content-Length (when present and honest) is a
// cheap pre-check, but chunked responses often carry no length at all — so we ALSO count bytes as the
// body is consumed and abort the instant the running total crosses MAX_FETCH_BYTES. We deliberately
// never call res.arrayBuffer()/res.text(): those buffer the entire payload first, which is exactly the
// OOM this cap exists to prevent.
async function readBodyCapped(
  res: Response
): Promise<{ ok: true; bytes: Uint8Array } | { ok: false; reason: string }> {
  const declared = Number(res.headers.get("content-length") ?? NaN);
  if (Number.isFinite(declared) && declared > MAX_FETCH_BYTES) {
    await res.body?.cancel().catch(() => {});
    return {
      ok: false,
      reason: `body too large: server declared ${declared} bytes > ${MAX_FETCH_BYTES} byte cap — size limit exceeded before reading`,
    };
  }
  if (!res.body) return { ok: true, bytes: new Uint8Array(0) };
  const hadUsableLength = Number.isFinite(declared) && declared >= 0;
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value || value.byteLength === 0) continue;
      total += value.byteLength;
      if (total > MAX_FETCH_BYTES) {
        await reader.cancel().catch(() => {});
        return {
          ok: false,
          reason: `body too large: response exceeded the ${MAX_FETCH_BYTES} byte cap after ${total} bytes read — counted while streaming and aborted mid-stream, not buffered (${
            hadUsableLength
              ? "server's Content-Length was smaller than the actual body"
              : "no Content-Length (chunked/streamed response)"
          })`,
        };
      }
      chunks.push(value);
    }
  } catch (e) {
    await reader.cancel().catch(() => {});
    return { ok: false, reason: `fetch failed while reading body: ${(e as Error).message}` };
  } finally {
    reader.releaseLock();
  }
  // Copy into one contiguous buffer only after the cap held — at most MAX_FETCH_BYTES is ever held.
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    bytes.set(c, offset);
    offset += c.byteLength;
  }
  return { ok: true, bytes };
}

// Test seam: production callers MUST NOT set these. They exist so the guard's own tests can point it
// at a loopback HTTP server (which the real per-hop private-IP check correctly rejects) and at a
// stubbed fetch, without weakening the defaults every real caller gets.
export interface SafeFetchDeps {
  fetchImpl?: typeof fetch;
  checkHost?: (host: string) => Promise<SsrfVerdict>;
}

// Fetch a URL as text with SSRF protection on EVERY hop: validate scheme/creds, resolve + check
// the host's IPs, fetch with redirect:"manual", and re-validate each Location before following.
// Bounded to maxHops (default 3) redirects. Never follows a redirect to a private/metadata host.
// Failure reasons are human-facing (they land in PR bodies and verify JSON), so they state exactly
// what happened: which hop the chain stopped on, or that the byte cap was exceeded.
export async function safeFetchText(
  rawUrl: string,
  opts: { timeoutMs?: number; maxHops?: number; deps?: SafeFetchDeps } = {}
): Promise<SafeFetchResult> {
  const maxHops = opts.maxHops ?? 3;
  const timeoutMs = opts.timeoutMs ?? 12_000;
  const fetchImpl = opts.deps?.fetchImpl ?? fetch;
  const checkHost = opts.deps?.checkHost ?? assertSafeHost;
  let current = rawUrl;
  let redirects = 0; // redirect responses followed so far (not counting the initial request)
  for (;;) {
    const urlCheck = assertSafePublicUrl(current);
    if (!urlCheck.ok) return { ok: false, reason: urlCheck.reason };
    const url = new URL(current);
    const hostCheck = await checkHost(url.hostname);
    if (!hostCheck.ok) return { ok: false, reason: hostCheck.reason };
    let res: Response;
    try {
      res = await fetchImpl(current, {
        redirect: "manual",
        signal: AbortSignal.timeout(timeoutMs),
        headers: { "user-agent": "leaderboard-discovery-apply/1.0 (+verify)" },
      });
    } catch (e) {
      return { ok: false, reason: `fetch failed: ${(e as Error).message}` };
    }
    // 2xx -> done. 3xx -> validate + follow one hop. Anything else -> unreachable (soft miss).
    if (res.status >= 200 && res.status < 300) {
      // Capture raw bytes (+ content-type) so PDF bodies can be routed through pdftotext instead of
      // a naive substring search (res.text() UTF-8-decodes compressed PDF streams -> false negatives).
      const body = await readBodyCapped(res);
      if (!body.ok) return { ok: false, reason: body.reason };
      return {
        ok: true,
        status: res.status,
        contentType: res.headers.get("content-type") ?? "",
        bytes: body.bytes,
        text: Buffer.from(body.bytes).toString("utf8"),
      };
    }
    if (res.status >= 300 && res.status < 400) {
      // A redirect body is never used; drop it so the socket can be reused/closed promptly.
      await res.body?.cancel().catch(() => {});
      const location = res.headers.get("location");
      if (!location) {
        return {
          ok: false,
          reason: `redirect ${res.status} without Location at ${shortUrl(current)} (after ${redirects} redirect(s))`,
        };
      }
      let next: string;
      try {
        next = new URL(location, current).toString(); // resolve relative redirects
      } catch {
        return {
          ok: false,
          reason: `redirect Location unparseable at ${shortUrl(current)}: ${location.slice(0, 80)}`,
        };
      }
      redirects++;
      if (redirects > maxHops) {
        // Names the real chain length and the exact hop that pushed it over the limit, plus where
        // that hop pointed — otherwise "exceeded N redirects" gives no way to diagnose the source.
        return {
          ok: false,
          reason: `redirect limit exceeded: ${redirects} redirect(s) encountered (max ${maxHops}); last hop ${shortUrl(current)} -> ${shortUrl(next)}`,
        };
      }
      current = next;
      continue;
    }
    await res.body?.cancel().catch(() => {});
    return { ok: false, reason: `HTTP ${res.status} at ${shortUrl(current)}` };
  }
}

// True when a fetched body is a PDF — by content-type, or the %PDF- magic bytes (some servers omit
// content-type). Used to route through pdftotext instead of a naive substring search.
export function isPdfBody(contentType: string, bytes: Uint8Array): boolean {
  if (/pdf/i.test(contentType)) return true;
  return (
    bytes.length >= 5 &&
    bytes[0] === 0x25 && // %
    bytes[1] === 0x50 && // P
    bytes[2] === 0x44 && // D
    bytes[3] === 0x46 // F
  );
}

// Outcomes of turning a fetched body into searchable text. Distinct failure statuses matter because
// the caller's verdict feeds dismissals: "cooccurrence-miss" reads as EVIDENCE THE SCORE IS ABSENT,
// while "we could not read the page" must not be treated as such. Two non-miss statuses:
//   - pdf-unverifiable: PDF whose text layer could not be extracted (pdftotext missing/failed, or an
//     image-only/scanned/encrypted PDF yielding no text).
//   - content-unreadable: the HTTP fetch SUCCEEDED but the body was empty or whitespace-only (e.g. a
//     JS-rendered page, a 204-style placeholder, or a truncated/misconfigured response). There is
//     simply no text to search, so reporting "score missing" would be wrong.
export type TextExtraction =
  | { ok: true; text: string }
  | { ok: false; status: "pdf-unverifiable" | "content-unreadable"; detail: string };

// Extract text suitable for substring attribution checks. HTML/text -> UTF-8 decode. PDF ->
// pdftotext, because raw `includes()` over compressed PDF bytes false-negatives: a source can
// genuinely contain the score + system name yet report hasName:false (verified 2026-06-28 on an
// openreview.net PDF).
//
// Security: `bytes` come from an already-SSRF-validated fetch. pdftotext reads a temp file whose name
// is process-generated; fetched text is NEVER placed in argv (fixed args ["-q", file, "-"]), so an
// attacker controlling the PDF body cannot inject shell arguments.
export async function extractSearchableText(
  bytes: Uint8Array,
  contentType: string
): Promise<TextExtraction> {
  if (!isPdfBody(contentType, bytes)) {
    const text = Buffer.from(bytes).toString("utf8");
    if (text.trim() === "") {
      return {
        ok: false,
        status: "content-unreadable",
        detail: `fetch succeeded but body was empty or whitespace-only (${bytes.length} byte(s), content-type "${contentType || "unknown"}")`,
      };
    }
    return { ok: true, text };
  }
  const file = path.join(tmpdir(), `dv-${process.pid}-${bytes.length}.pdf`);
  try {
    await writeFile(file, bytes);
    const { stdout } = await execFileP("pdftotext", ["-q", file, "-"], {
      maxBuffer: 24 * 1024 * 1024,
      timeout: 20_000,
    });
    // pdftotext exits 0 with "" on an image-only/scanned/encrypted PDF (no extractable text layer).
    if (stdout.trim() === "") {
      return {
        ok: false,
        status: "pdf-unverifiable",
        detail:
          "PDF source; pdftotext extracted no text (image-only/scanned/encrypted) — text not extractable",
      };
    }
    return { ok: true, text: stdout };
  } catch (e) {
    return {
      ok: false,
      status: "pdf-unverifiable",
      detail: `PDF source; pdftotext unavailable or failed — text not extracted (${(e as Error).message})`,
    };
  } finally {
    await unlink(file).catch(() => {});
  }
}

function checkIp(ip: string): SsrfVerdict {
  if (net.isIPv4(ip)) return checkIpv4(ip);
  if (net.isIPv6(ip)) return checkIpv6(ip);
  return { ok: false, reason: `not an IP: ${ip}` };
}

// Mirrors @steel-dev/atlas safety.js isPrivateIPv4Octets.
function checkIpv4(ip: string): SsrfVerdict {
  const parts = ip.split(".").map(Number);
  if (parts.length !== 4 || parts.some((n) => Number.isNaN(n) || n < 0 || n > 255)) {
    return { ok: false, reason: `bad IPv4 ${ip}` };
  }
  const [a, b] = parts;
  const blocked =
    a === 0 || // 0.0.0.0/8 "this host"
    a === 10 || // 10.0.0.0/8 private
    a === 127 || // 127.0.0.0/8 loopback
    (a === 100 && b >= 64 && b <= 127) || // 100.64.0.0/10 CGNAT
    (a === 169 && b === 254) || // 169.254.0.0/16 link-local + cloud metadata (169.254.169.254)
    (a === 172 && b >= 16 && b <= 31) || // 172.16.0.0/12 private
    (a === 192 && b === 168); // 192.168.0.0/16 private
  return blocked ? { ok: false, reason: `private/loopback/link-local IPv4 ${ip}` } : { ok: true };
}

function checkIpv6(ip: string): SsrfVerdict {
  const v = ip.toLowerCase();
  if (v === "::1") return { ok: false, reason: `loopback IPv6 ${ip}` };
  if (v === "::" || v.startsWith("::ffff:0:0"))
    return { ok: false, reason: `unspecified IPv6 ${ip}` };
  if (v.startsWith("fe80")) return { ok: false, reason: `link-local IPv6 ${ip}` };
  if (v.startsWith("fc") || v.startsWith("fd")) return { ok: false, reason: `ULA IPv6 ${ip}` };
  // IPv4-mapped that hide a private v4 (e.g. ::ffff:127.0.0.1).
  if (v.startsWith("::ffff:")) {
    const inner = v.slice("::ffff:".length);
    if (net.isIPv4(inner)) return checkIpv4(inner);
  }
  return { ok: true };
}
