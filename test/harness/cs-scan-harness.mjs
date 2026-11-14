// ── Execute the REAL content-script incremental scan under Node ───────────────
// The scan lives inside extension/cs-src/*.js, which is a browser IIFE. The bugs
// this harness guards against are BEHAVIOURAL — "a scroll reported 12 removed /
// 40 added", "opening a dialog lost its controls", "a genuine removal vanished" —
// so a source grep cannot see them. Instead the exact functions are lifted out of
// the BUILT artifact and run against the fake DOM in ./fake-dom.mjs, which lets a
// test move elements' rects (a scroll) or toggle a dialog's display (open/close).
//
// The harness deliberately calls the same functions the content script does:
// collectScan() -> membership, extractState() -> visible/inViewport,
// scanFingerprintOf() -> fp, and assignRef() -> refs. What it does NOT re-create
// is exploreIncremental()'s plumbing (forms, enrichment, full-SAG escalation);
// the diff itself is exercised through src/incr.js diffScan(), the canonical
// implementation the content script mirrors. The diff inputs come from
// collectScanWithRefs(), the exact function the content script diffs, so the
// ref-stability path is real: it reads the same elementSignatures cache.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const ARTIFACT = fileURLToPath(new URL('../../extension/websense-cs.js', import.meta.url));

// Names the scan path depends on, in dependency order. Each is extracted by
// brace-matching the artifact, so the harness cannot drift from the shipped code.
const SCAN_FUNCS = [
  'collectScan', 'collectScanWithRefs', 'collectScanCandidates',
  '_isScanCandidate', '_isCandidateNode',
  '_collectCursorSelectorTexts', '_cursorSelectorText', '_scanPresent',
  'cachedStyle', '_cachedClassify', 'getAttrs', 'getLabel', 'fullText',
  'pseudoContent', 'isSensitiveValueField', 'detectDisabledReason', 'extractState',
  'rectInViewport', 'wsIdentityKey', 'scanFingerprintOf', 'wsFieldChanges',
  'getAllElements', 'sameOrigin', 'isVisible', 'isInViewport',
];

// Extract a top-level `function name(` body from a source string by brace
// matching, skipping strings and comments and REGEX literals so their brackets
// do not confuse the counter.
export function extractFunction(src, name) {
  const idx = src.indexOf('function ' + name + '(');
  if (idx < 0) throw new Error('cs-scan-harness: function ' + name + ' not found in the artifact');
  let i = src.indexOf('{', idx);
  let depth = 0, inS = false, inD = false, inC = false, inT = false, inRe = false;
  let prevSignificant = '';
  for (; i < src.length; i++) {
    const c = src[i], n = src[i + 1];
    if (inS) { if (c === '\\') { i++; continue; } if (c === "'") inS = false; continue; }
    if (inD) { if (c === '\\') { i++; continue; } if (c === '"') inD = false; continue; }
    if (inT) { if (c === '\\') { i++; continue; } if (c === '`') inT = false; continue; }
    if (inRe) { if (c === '\\') { i++; continue; } if (c === '/') inRe = false; continue; }
    if (inC) { if (c === '\n') inC = false; continue; }
    if (c === '/' && n === '/') { inC = true; i++; continue; }
    if (c === '/' && n === '*') { const e = src.indexOf('*/', i + 2); i = e < 0 ? src.length : e + 1; continue; }
    // A '/' that begins a REGEX literal rather than a division: the previous
    // significant character cannot end an expression. This is what tripped the
    // extractor on pseudoContent's .replace(/^["']|["']$/g, '').
    if (c === '/' && !inS && !inD && !inT && /^(?:|[(,=:;\[!&|?{}~^<>+\-*%])$/.test(prevSignificant)) { inRe = true; continue; }
    if (c === "'") { inS = true; prevSignificant = c; continue; }
    if (c === '"') { inD = true; prevSignificant = c; continue; }
    if (c === '`') { inT = true; prevSignificant = c; continue; }
    if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) return src.slice(idx, i + 1); }
    if (!/\s/.test(c)) prevSignificant = c;
  }
  throw new Error('cs-scan-harness: unterminated function ' + name);
}

const ARTIFACT_SRC = readFileSync(ARTIFACT, 'utf8');
const SCAN_CODE = SCAN_FUNCS.map((n) => extractFunction(ARTIFACT_SRC, n)).join('\n\n');

/**
 * Build a runnable scan API bound to one fake document/window.
 * Returns { collectScan, assignRef, scanEntries }.
 *   scanEntries() → Array<{key, fp, fpo, label, ref}> — the scan cache's
 *   contents, with refs assigned exactly as collectScanWithRefs() does.
 */
export function makeScanHarness(doc, win) {
  const elementSignatures = new WeakMap();
  const box = { cache: new WeakMap() };
  const factory = new Function(
    'document', 'window', 'Node', 'CSS', 'wsLog', 'elementSignatures', 'box',
    `
    let refCounter = 0;
    function assignRef(el) {
      if (elementSignatures.has(el)) return elementSignatures.get(el).ref;
      const ref = 'E' + (refCounter++);
      elementSignatures.set(el, { ref: ref });
      try { el.setAttribute('data-websense-ref', ref); } catch (_) {}
      return ref;
    }
    let _styleCache = box.cache;
    function _styleCacheClear() { _styleCache = new WeakMap(); }
    ${SCAN_CODE}
    return { collectScan, collectScanWithRefs, assignRef };
    `,
  );
  const NodeShim = { ELEMENT_NODE: 1 };
  const CSSShim = { escape: (s) => String(s).replace(/["\\]/g, '\\$&') };
  const api = factory(doc, win, NodeShim, CSSShim, () => {}, elementSignatures, box);

  function scanEntries() {
    const out = [];
    for (const [key, e] of api.collectScanWithRefs()) {
      out.push({ key, fp: e.fp, fpo: e.fpo, label: e.label, ref: e.ref, el: e.el });
    }
    return out;
  }
  return {
    collectScan: api.collectScan,
    collectScanWithRefs: api.collectScanWithRefs,
    assignRef: api.assignRef,
    scanEntries,
    // The signature cache the REAL scan reads for ref stability.
    elementSignatures,
  };
}
