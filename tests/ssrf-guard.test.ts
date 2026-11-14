// Tests for the discovery soft-verify fetch guard's NEW outcomes: streaming size cap (chunked, no
// Content-Length), redirect-limit diagnostics, and the empty/whitespace-only body verdict. Each test
// runs against a loopback HTTP server the test itself starts, so the real network stack is exercised.
// The per-hop private-IP check is bypassed ONLY through the explicit test seam (deps.checkHost) —
// production callers never pass it, so the guard's real SSRF constraints stay intact.

import assert from "node:assert/strict";
import { test } from "node:test";
import http from "node:http";
import type { AddressInfo } from "node:net";
import { safeFetchText, extractSearchableText } from "../src/lib/ssrf-guard.js";
import type { SafeFetchDeps } from "../src/lib/ssrf-guard.js";
import { evaluateFetchedEvidence } from "../src/lib/evidence-verify.js";

const MIB = 1024 * 1024;
// The guard's real cap is 16 MiB. The stub server intentionally streams far more than that so the
// test can prove the read is aborted mid-stream rather than buffered to completion.
const OVERSIZE_TARGET = 96 * MIB;

// Loopback is private by design; override ONLY the host check for these local stubs.
const loopbackDeps: SafeFetchDeps = { checkHost: async () => ({ ok: true }) };

async function startServer(handler: http.RequestListener): Promise<{
  url: string;
  close: () => Promise<void>;
}> {
  const server = http.createServer(handler);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  return {
    url: `http://127.0.0.1:${port}`,
    close: () =>
      new Promise<void>((resolve) => {
        server.closeAllConnections();
        server.close(() => resolve());
      }),
  };
}

test("chunked body without Content-Length is capped while streaming, not buffered first", async () => {
  let bytesWritten = 0;
  let finished = false;
  const srv = await startServer((_req, res) => {
    res.writeHead(200, { "content-type": "text/html" }); // no content-length -> chunked
    const chunk = Buffer.alloc(64 * 1024, 0x61); // 'a'
    const pump = (): void => {
      if (res.destroyed || res.writableEnded) return;
      if (bytesWritten >= OVERSIZE_TARGET) {
        finished = true;
        res.end();
        return;
      }
      try {
        if (res.write(chunk)) setImmediate(pump);
        else res.once("drain", pump);
      } catch {
        // client aborted mid-stream — expected when the cap trips
      }
      bytesWritten += chunk.byteLength;
    };
    res.on("error", () => {});
    pump();
  });
  try {
    const r = await safeFetchText(`${srv.url}/huge`, {
      timeoutMs: 20_000,
      deps: loopbackDeps,
    });
    assert.equal(r.ok, false);
    if (r.ok) return;
    assert.match(r.reason, /body too large/i);
    assert.match(r.reason, /16 byte cap|16777216 byte cap/);
    assert.match(r.reason, /aborted mid-stream|streamed response/i);
    // The server never delivered the whole payload: the reader hung up shortly after the cap.
    assert.equal(finished, false, "client must abort before the oversized body completes");
    assert.ok(
      bytesWritten < OVERSIZE_TARGET / 2,
      `server should stop well short of ${OVERSIZE_TARGET} bytes, wrote ${bytesWritten}`
    );
  } finally {
    await srv.close();
  }
});

test("a normal chunked body under the cap is read completely and intact", async () => {
  const body = "Score 74.1 for Example-Agent. ".repeat(4096); // ~114 KiB, several chunks
  const srv = await startServer((_req, res) => {
    res.writeHead(200, { "content-type": "text/html" });
    res.write(body.slice(0, 50_000));
    res.end(body.slice(50_000));
  });
  try {
    const r = await safeFetchText(`${srv.url}/ok`, { deps: loopbackDeps });
    assert.equal(r.ok, true);
    if (!r.ok) return;
    assert.equal(r.text, body);
    assert.equal(r.bytes.byteLength, Buffer.byteLength(body));
  } finally {
    await srv.close();
  }
});

test("redirect limit names the real hop count and the last hop that was refused", async () => {
  const srv = await startServer((req, res) => {
    const m = /^\/r\/(\d+)$/.exec(req.url ?? "");
    if (!m) {
      res.writeHead(404).end();
      return;
    }
    const n = Number(m[1]);
    res.writeHead(302, { location: n >= 6 ? "/final" : `/r/${n + 1}` });
    res.end();
  });
  const esc = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  try {
    const start = `${srv.url}/r/1`;
    const r = await safeFetchText(start, { maxHops: 3, deps: loopbackDeps });
    assert.equal(r.ok, false);
    if (r.ok) return;
    // maxHops 3 -> redirects #1..#3 followed; #4 is the one refused, at /r/4 pointing at /r/5.
    assert.match(r.reason, /redirect limit exceeded/i);
    assert.match(r.reason, /4 redirect\(s\) encountered \(max 3\)/);
    assert.match(r.reason, new RegExp(`${esc(srv.url)}/r/4`));
    assert.match(r.reason, new RegExp(`${esc(srv.url)}/r/5`));
    // The reason is bounded so a hostile long URL cannot bloat a PR body.
    const longStart = `${srv.url}/r/1?pad=${"x".repeat(400)}`;
    const r2 = await safeFetchText(longStart, { maxHops: 3, deps: loopbackDeps });
    assert.equal(r2.ok, false);
    if (r2.ok) return;
    assert.ok(r2.reason.length < 600, `reason should stay short, got ${r2.reason.length} chars`);
  } finally {
    await srv.close();
  }
});

test("redirect chain within the limit reaches the target", async () => {
  const srv = await startServer((req, res) => {
    if (req.url === "/a") {
      res.writeHead(301, { location: "/b" }).end();
      return;
    }
    if (req.url === "/b") {
      res.writeHead(302, { location: "/c" }).end();
      return;
    }
    res.writeHead(200, { "content-type": "text/plain" }).end("hello");
  });
  try {
    const r = await safeFetchText(`${srv.url}/a`, { maxHops: 3, deps: loopbackDeps });
    assert.equal(r.ok, true);
    if (!r.ok) return;
    assert.equal(r.text, "hello");
  } finally {
    await srv.close();
  }
});

test("successful fetch with an empty or whitespace-only body is content-unreadable, not a miss", async () => {
  const srv = await startServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(req.url === "/blank" ? "" : "  \n\t  \r\n ");
  });
  try {
    for (const p of ["/blank", "/spaces"]) {
      const f = await safeFetchText(`${srv.url}${p}`, { deps: loopbackDeps });
      assert.equal(f.ok, true);
      if (!f.ok) return;
      const ex = await extractSearchableText(f.bytes, f.contentType);
      assert.equal(ex.ok, false);
      if (ex.ok) return;
      assert.equal(ex.status, "content-unreadable");
      assert.match(ex.detail, /empty or whitespace-only/i);
    }
  } finally {
    await srv.close();
  }
});

test("a body with text still extracts, and a broken PDF stays pdf-unverifiable", async () => {
  const html = Buffer.from("<table><tr><td>Example-Agent</td><td>74.1</td></tr></table>");
  const ex = await extractSearchableText(new Uint8Array(html), "text/html");
  assert.equal(ex.ok, true);
  if (!ex.ok) return;
  assert.match(ex.text, /Example-Agent/);
  assert.match(ex.text, /74\.1/);

  // Minimal malformed PDF: pdftotext fails or yields nothing -> pdf-unverifiable (NOT a co-occurrence
  // miss), preserving the pre-existing PDF behaviour.
  const pdf = new Uint8Array(Buffer.from("%PDF-1.4\nnot a real pdf\n"));
  const pdfEx = await extractSearchableText(pdf, "application/pdf");
  assert.equal(pdfEx.ok, false);
  if (pdfEx.ok) return;
  assert.equal(pdfEx.status, "pdf-unverifiable");
});

test("shared verdict maps unreadable bodies away from cooccurrence-miss", async () => {
  const html = Buffer.from("<td>Example-Agent</td><td>74.1</td>");
  const ok = await evaluateFetchedEvidence(
    new Uint8Array(html),
    "text/html",
    74.1,
    "Example-Agent"
  );
  assert.equal(ok.status, "verified");
  assert.equal(ok.hasNum, true);
  assert.equal(ok.hasName, true);

  const miss = await evaluateFetchedEvidence(
    new Uint8Array(Buffer.from("<td>Other-Agent</td><td>60</td>")),
    "text/html",
    74.1,
    "Example-Agent"
  );
  assert.equal(miss.status, "cooccurrence-miss");

  const blank = await evaluateFetchedEvidence(
    new Uint8Array(0),
    "text/html",
    74.1,
    "Example-Agent"
  );
  assert.equal(blank.status, "content-unreadable");
  assert.equal(blank.hasNum, false);

  const pdf = new Uint8Array(Buffer.from("%PDF-1.4\nnope\n"));
  const pdfV = await evaluateFetchedEvidence(pdf, "application/pdf", 74.1, "Example-Agent");
  assert.equal(pdfV.status, "pdf-unverifiable");
});
