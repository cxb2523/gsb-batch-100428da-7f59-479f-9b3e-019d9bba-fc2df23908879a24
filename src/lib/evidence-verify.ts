// ABOUTME: Shared evidence-verdict logic for the discovery pipeline's soft re-fetch. Both the APPLY
// ABOUTME: step (discover-apply softVerify) and the REVIEW wrapper (discover-verify) must agree on what
// ABOUTME: a fetched page proves, and — critically — on which outcomes are TOOLING LIMITATIONS rather
// ABOUTME: than evidence of a co-occurrence miss: pdf-unverifiable and content-unreadable.

import { extractSearchableText } from "./ssrf-guard.js";

// Every outcome of checking a successfully fetched body for the score + system name.
//   verified            — score text and an attributable name token both appear.
//   cooccurrence-miss   — readable body, but one/both are absent: grounds to question attribution.
//   pdf-unverifiable    — a PDF whose text layer we could not extract: NOT evidence of absence.
//   content-unreadable  — fetch succeeded but the body was empty/whitespace-only: NOT evidence of absence.
export type EvidenceStatus =
  | "verified"
  | "cooccurrence-miss"
  | "pdf-unverifiable"
  | "content-unreadable";

export interface EvidenceVerdict {
  status: EvidenceStatus;
  detail?: string;
  hasNum: boolean;
  hasName: boolean;
}

export function longestToken(name: string): string {
  const toks = name.match(/[A-Za-z0-9]{3,}/g) ?? [];
  return toks.sort((a, b) => b.length - a.length)[0] ?? "";
}

// Pure attribution check over already-extracted text. Short system names (e.g. "o3") yield no 3+ char
// token — longestToken returns "" — and in that case we do NOT pass attribution trivially: the FULL
// systemName must co-occur with the score, else the number may belong to a different table row.
export function checkCooccurrence(
  text: string,
  scoreValue: number | null,
  systemName: string
): { hasNum: boolean; hasName: boolean } {
  const num = scoreValue != null && Number.isFinite(scoreValue) ? String(scoreValue) : "";
  const hasNum = num !== "" && text.includes(num);
  const token = longestToken(systemName);
  const name = systemName.toLowerCase().trim();
  const hasName =
    token !== ""
      ? text.toLowerCase().includes(token.toLowerCase())
      : name !== "" && text.toLowerCase().includes(name);
  return { hasNum, hasName };
}

// Turn a fetched body into a verdict, folding in the two "we could not read this" outcomes so callers
// never downgrade an add on the strength of a page they simply couldn't parse.
export async function evaluateFetchedEvidence(
  bytes: Uint8Array,
  contentType: string,
  scoreValue: number | null,
  systemName: string
): Promise<EvidenceVerdict> {
  const ex = await extractSearchableText(bytes, contentType);
  if (!ex.ok) return { status: ex.status, detail: ex.detail, hasNum: false, hasName: false };
  const { hasNum, hasName } = checkCooccurrence(ex.text, scoreValue, systemName);
  if (hasNum && hasName) return { status: "verified", hasNum, hasName };
  return {
    status: "cooccurrence-miss",
    detail: `fetched body did not contain both score "${scoreValue != null ? String(scoreValue) : ""}" and system token "${longestToken(systemName)}"`,
    hasNum,
    hasName,
  };
}
