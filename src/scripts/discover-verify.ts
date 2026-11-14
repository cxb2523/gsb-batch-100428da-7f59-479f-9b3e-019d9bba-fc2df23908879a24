// ABOUTME: SSRF-guarded evidence verify helper for the discovery REVIEW pass. A tool-bearing review
// ABOUTME: agent (claude-code-action) calls this instead of curl/fetch directly, so every network hop
// ABOUTME: is forced through src/lib/ssrf-guard.ts (http(s)-only, per-hop DNS check, private/loopback/
// ABOUTME: link-local/169.254.169.254 metadata IPs blocked). Prints a JSON verdict: did the fetched
// ABOUTME: source contain BOTH the score and an attributable system-name token? Unreadable bodies are
// ABOUTME: reported as pdf-unverifiable / content-unreadable — never as a cooccurrence miss.
// ABOUTME: Usage: node dist/scripts/discover-verify.js <url> <scoreValue> <systemName>

import { safeFetchText } from "../lib/ssrf-guard.js";
import { evaluateFetchedEvidence } from "../lib/evidence-verify.js";

function usage(): never {
  console.error("Usage: discover-verify <url> <scoreValue> <systemName>");
  process.exit(2);
}

const [, , url, scoreArg, systemName] = process.argv;
if (!url || !scoreArg || !systemName) usage();
const scoreValue = Number(scoreArg);

async function main(): Promise<void> {
  const f = await safeFetchText(url, { timeoutMs: 15_000 });
  if (!f.ok) {
    console.log(JSON.stringify({ status: "unreachable", reason: f.reason }));
    return;
  }
  // Route PDF bodies through pdftotext; raw includes() over compressed PDF bytes false-negatives.
  // Unreadable bodies get their own status (pdf-unverifiable / content-unreadable), never a
  // co-occurrence miss: the review pass must not treat "couldn't read it" as "score is absent".
  const v = await evaluateFetchedEvidence(
    f.bytes,
    f.contentType,
    Number.isFinite(scoreValue) ? scoreValue : null,
    systemName
  );
  console.log(
    JSON.stringify({
      status: v.status,
      ...(v.detail ? { reason: v.detail } : {}),
      hasNum: v.hasNum,
      hasName: v.hasName,
      fetchedBytes: f.bytes.length,
    })
  );
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
