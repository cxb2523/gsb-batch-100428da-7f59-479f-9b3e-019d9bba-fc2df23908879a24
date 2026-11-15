'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { Loader2, AlertTriangle, RotateCcw } from 'lucide-react';
import type { Product } from '@/data/products';
import ProductCard from './ProdcutCard';

interface BatchResponse {
  data: Product[];
  count: number;
  nextCursor: string | null;
  hasMore: boolean;
  total: number;
}

const InfiniteScroll = () => {
  const [items, setItems] = useState<Product[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(true);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loaderRef = useRef<HTMLDivElement>(null);
  const inFlightRef = useRef(false);
  // Ids already on screen — guards against duplicates even if a batch is
  // fetched twice (e.g. strict-mode double effects or a retried request
  // that actually succeeded server-side).
  const loadedIdsRef = useRef<Set<number>>(new Set());
  // Cursor of the batch that failed, so retry resumes exactly there —
  // no re-fetch of already shown items, no skipped batch.
  const failedCursorRef = useRef<string | null>(null);

  const loadBatch = useCallback(async (cursor: string | null) => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setLoading(true);
    setError(null);
    try {
      const url = cursor
        ? `/api/products?cursor=${encodeURIComponent(cursor)}`
        : '/api/products';
      const res = await fetch(url);
      const body = (await res.json()) as BatchResponse & {
        error?: { code: string; message: string };
      };
      if (!res.ok) {
        throw new Error(
          body.error?.message ?? `Request failed with status ${res.status}`,
        );
      }
      const batch = body;
      setItems((prev) => {
        const fresh = batch.data.filter((p) => !loadedIdsRef.current.has(p.id));
        for (const p of fresh) loadedIdsRef.current.add(p.id);
        return [...prev, ...fresh];
      });
      setNextCursor(batch.nextCursor);
      setHasMore(batch.hasMore);
      setTotal(batch.total);
    } catch (err) {
      failedCursorRef.current = cursor;
      setError(
        err instanceof Error ? err.message : 'Failed to load products.',
      );
    } finally {
      inFlightRef.current = false;
      setLoading(false);
    }
  }, []);

  // Initial batch.
  useEffect(() => {
    loadBatch(null);
  }, [loadBatch]);

  const loadMore = useCallback(() => {
    if (loading || !hasMore || error) return;
    loadBatch(nextCursor);
  }, [loading, hasMore, error, nextCursor, loadBatch]);

  const retry = useCallback(() => {
    if (loading) return;
    loadBatch(failedCursorRef.current);
  }, [loading, loadBatch]);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) loadMore();
      },
      { threshold: 0.1 },
    );
    if (loaderRef.current) observer.observe(loaderRef.current);
    return () => observer.disconnect();
  }, [loadMore]);

  return (
    <>
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
        {items.map((product) => (
          <ProductCard key={product.id} product={product} />
        ))}
      </div>

      <div ref={loaderRef} className="flex flex-col items-center gap-3 py-10">
        {loading && (
          <div className="flex items-center gap-2 text-sm font-mono text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin text-primary" />
            Loading more products...
          </div>
        )}

        {error && !loading && (
          <div className="flex flex-col items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-center">
            <p className="flex items-center gap-2 text-sm font-mono text-destructive">
              <AlertTriangle className="h-4 w-4" />
              Failed to load the next batch: {error}
            </p>
            <button
              type="button"
              onClick={retry}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-1.5 text-xs font-mono text-foreground transition-colors hover:bg-secondary"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              Retry
            </button>
          </div>
        )}

        {!hasMore && !error && total !== null && (
          <p className="text-sm font-mono text-muted-foreground">
            All {total} products loaded.
          </p>
        )}
      </div>
    </>
  );
};

export default InfiniteScroll;
