import { createHmac } from 'node:crypto';

/**
 * Opaque, tamper-evident cursors for the /api/products endpoint.
 *
 * A cursor encodes the offset of the NEXT batch together with an HMAC
 * signature, so a client (or scraper) cannot invent or alter a cursor
 * without the server noticing. Forged or malformed cursors are rejected
 * with an explicit error instead of silently returning an empty page.
 */

const CURSOR_SECRET =
  process.env.PRODUCTS_CURSOR_SECRET ?? 'scraping-sandbox-products-cursor-v1';

function sign(offset: number): string {
  return createHmac('sha256', CURSOR_SECRET)
    .update(`products:${offset}`)
    .digest('hex')
    .slice(0, 16);
}

export function encodeCursor(offset: number): string {
  return `${offset}.${sign(offset)}`;
}

export type DecodeCursorResult =
  | { ok: true; offset: number }
  | { ok: false; reason: string };

export function decodeCursor(cursor: string): DecodeCursorResult {
  const match = /^(\d+)\.([0-9a-f]{16})$/.exec(cursor);
  if (!match) {
    return {
      ok: false,
      reason:
        'Malformed cursor. Expected the opaque cursor string returned by a previous response.',
    };
  }
  const offset = Number.parseInt(match[1], 10);
  if (!Number.isSafeInteger(offset)) {
    return { ok: false, reason: 'Cursor offset is not a valid integer.' };
  }
  if (sign(offset) !== match[2]) {
    return {
      ok: false,
      reason: 'Cursor signature mismatch. The cursor was forged or corrupted.',
    };
  }
  return { ok: true, offset };
}
