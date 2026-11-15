import { NextRequest, NextResponse } from 'next/server';
import { products } from '@/data/products';
import { decodeCursor, encodeCursor } from '@/lib/product-cursor';

export const dynamic = 'force-dynamic';

const DEFAULT_LIMIT = 20;
const MAX_LIMIT = 20;

interface ErrorBody {
  error: {
    code: string;
    message: string;
  };
}

function errorResponse(status: number, code: string, message: string) {
  const body: ErrorBody = { error: { code, message } };
  return NextResponse.json(body, { status });
}

/**
 * GET /api/products
 *
 * Cursor-paginated product feed for external collectors.
 *
 * Query params:
 *   - cursor (optional): opaque cursor from a previous response. Omit for
 *     the first batch.
 *   - limit  (optional): batch size, 1..20 (default 20).
 *
 * Success (200):
 *   {
 *     data: Product[],       // this batch (in stable id order)
 *     count: number,         // items in this batch
 *     nextCursor: string|null, // pass as `cursor` for the next batch; null at the end
 *     hasMore: boolean,      // false once the final batch has been served
 *     total: number          // total number of products
 *   }
 *
 * Errors (never an empty list masquerading as success):
 *   - 400 INVALID_CURSOR      malformed or forged cursor
 *   - 400 INVALID_LIMIT       limit not an integer in 1..20
 *   - 416 CURSOR_OUT_OF_RANGE cursor points past the end of the dataset
 */
export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const total = products.length;

  // --- limit ---
  let limit = DEFAULT_LIMIT;
  const limitParam = params.get('limit');
  if (limitParam !== null) {
    const parsed = Number(limitParam);
    if (!Number.isInteger(parsed) || parsed < 1 || parsed > MAX_LIMIT) {
      return errorResponse(
        400,
        'INVALID_LIMIT',
        `Invalid limit "${limitParam}". Must be an integer between 1 and ${MAX_LIMIT}.`,
      );
    }
    limit = parsed;
  }

  // --- cursor ---
  let offset = 0;
  const cursorParam = params.get('cursor');
  if (cursorParam !== null && cursorParam !== '') {
    const decoded = decodeCursor(cursorParam);
    if (!decoded.ok) {
      return errorResponse(400, 'INVALID_CURSOR', decoded.reason);
    }
    if (decoded.offset > total) {
      return errorResponse(
        416,
        'CURSOR_OUT_OF_RANGE',
        `Cursor offset ${decoded.offset} is beyond the end of the dataset (${total} products).`,
      );
    }
    offset = decoded.offset;
  }

  const batch = products.slice(offset, offset + limit);
  const nextOffset = offset + batch.length;
  const hasMore = nextOffset < total;

  return NextResponse.json({
    data: batch,
    count: batch.length,
    nextCursor: hasMore ? encodeCursor(nextOffset) : null,
    hasMore,
    total,
  });
}
