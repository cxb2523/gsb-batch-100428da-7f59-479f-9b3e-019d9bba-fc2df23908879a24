/* Encoding helpers matching the obfuscation used by index.html:
   payload -> UTF-8 bytes -> XOR with repeating key -> base64 -> split into 10 chunks. */
'use strict';

const KEY_B64 = 'c2fwdHJVHgaayDvkPWnzTFjvRaRmIEtF';

function keyBytes() {
  return Buffer.from(KEY_B64, 'base64');
}

function xorBytes(buf, key) {
  const out = Buffer.alloc(buf.length);
  for (let i = 0; i < buf.length; i++) out[i] = buf[i] ^ key[i % key.length];
  return out;
}

function encodePayload(html) {
  const key = keyBytes();
  const xored = xorBytes(Buffer.from(html, 'utf8'), key);
  return xored.toString('base64');
}

function decodePayload(b64Joined) {
  const key = keyBytes();
  const raw = Buffer.from(b64Joined, 'base64');
  return xorBytes(raw, key).toString('utf8');
}

function splitChunks(b64, n) {
  const size = Math.ceil(b64.length / n);
  const chunks = [];
  for (let i = 0; i < n; i++) chunks.push(b64.slice(i * size, (i + 1) * size));
  while (chunks.length < n) chunks.push('');
  return chunks;
}

module.exports = { encodePayload, decodePayload, splitChunks, KEY_B64 };
