/* Generates obfuscated landing pages from the REAL index.html shell,
   replacing only the ten hidden payload chunks. Generated pages therefore
   execute the exact same rebuild chain as the production landing page. */
'use strict';

const fs = require('fs');
const path = require('path');
const { encodePayload, splitChunks } = require('./codec');

const CHUNK_COUNT = 10;
const CHUNK_ID_PREFIX = '3gZUgFcX7';

function buildPage(shellHtml, payloadHtml) {
  const chunks = splitChunks(encodePayload(payloadHtml), CHUNK_COUNT);
  let out = shellHtml;
  chunks.forEach((chunk, i) => {
    const re = new RegExp('(id="' + CHUNK_ID_PREFIX + i + '" style="display:none">)[^<]*(</div>)');
    if (!re.test(out)) throw new Error('chunk slot ' + i + ' not found in shell');
    out = out.replace(re, '$1' + chunk + '$2');
  });
  return out;
}

const LISTENER =
  "<script>document.addEventListener('__rebuildScriptReport__'," +
  "function(e){window.__lastReportEvent=e.detail;});</script>";

const PAYLOADS = {
  'all-success.html': [
    '<!DOCTYPE html>',
    '<html lang="en"><head>',
    '<meta charset="utf-8">',
    '<title>all-success-case</title>',
    '<meta name="test-case" content="all-success">',
    '<base href="/">',
    '<link rel="stylesheet" href="/ext/sheet.css">',
    '<style>body{color:#111}</style>',
    LISTENER,
    '<script src="/ext/a1.js"></script>',
    '<iframe src="about:blank"></iframe>',
    '</head><body>',
    '<div id="marker">start</div>',
    "<script>window.__order=window.__order||[];window.__order.push('inline-1');</script>",
    '<script src="/ext/a2.js"></script>',
    "<script>window.__order.push('inline-2');document.getElementById('marker').textContent='done';</script>",
    '</body></html>',
  ].join('\n'),

  'external-fail.html': [
    '<!DOCTYPE html>',
    '<html lang="en"><head>',
    '<meta charset="utf-8">',
    '<title>external-fail-case</title>',
    LISTENER,
    '<script src="/ext/b1.js"></script>',
    '</head><body>',
    '<div id="marker">start</div>',
    "<script>window.__order=window.__order||[];window.__order.push('b-inline-1');</script>",
    '<script src="/ext/missing-404.js"></script>',
    '<script src="/ext/b2.js"></script>',
    "<script>window.__order.push('b-inline-2');document.getElementById('marker').textContent='done';</script>",
    '</body></html>',
  ].join('\n'),

  'inline-throw.html': [
    '<!DOCTYPE html>',
    '<html lang="en"><head>',
    '<meta charset="utf-8">',
    '<title>inline-throw-case</title>',
    LISTENER,
    '<script src="/ext/c1.js"></script>',
    '</head><body>',
    '<div id="marker">start</div>',
    "<script>window.__order=window.__order||[];window.__order.push('before-throw');throw new Error('boom-inline-xyz');</script>",
    '<script src="/ext/c2.js"></script>',
    "<script>window.__order.push('c-inline-2');document.getElementById('marker').textContent='done';</script>",
    '</body></html>',
  ].join('\n'),
};

function generateAll(shellHtml, outDir) {
  fs.mkdirSync(outDir, { recursive: true });
  for (const [name, payload] of Object.entries(PAYLOADS)) {
    fs.writeFileSync(path.join(outDir, name), buildPage(shellHtml, payload));
  }
  return Object.keys(PAYLOADS);
}

module.exports = { buildPage, generateAll, PAYLOADS };

if (require.main === module) {
  const repoRoot = path.join(__dirname, '..', '..');
  const shell = fs.readFileSync(path.join(repoRoot, 'index.html'), 'utf8');
  const outDir = path.join(__dirname, '..', 'samples');
  const names = generateAll(shell, outDir);
  console.log('generated:\n' + names.map((n) => '  tests/samples/' + n).join('\n'));
}
