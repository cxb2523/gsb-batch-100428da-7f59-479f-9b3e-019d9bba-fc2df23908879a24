import type { Prisma } from '@prisma/client';
import { prisma } from '..';

/**
 * The subset of the Prisma client used to record a completed sitemap part.
 * Accepting it explicitly lets the caller write the ledger row in the same
 * transaction as that part's articles.
 */
export type SitemapPartProgressClient = Pick<
  Prisma.TransactionClient,
  'sitemapPartProgress'
>;

/**
 * Returns the lastmod each sitemap part was successfully completed at.
 *
 * The per-part ledger replaces the single `sitemap` progress watermark as the
 * resume source: a partially successful round can record exactly the parts it
 * finished, so failed parts stay due on the next run.
 */
export async function getCompletedSitemapParts(): Promise<Map<string, string>> {
  const rows = await prisma.sitemapPartProgress.findMany({
    select: { loc: true, lastmod: true },
  });
  return new Map(rows.map((row) => [row.loc, row.lastmod]));
}

/**
 * Records that a part at `loc` was completed at its current index lastmod.
 * Must be called after the part's article writes have been committed.
 */
export async function markSitemapPartCompleted(
  client: SitemapPartProgressClient,
  loc: string,
  lastmod: string,
): Promise<void> {
  const completedAt = new Date().toISOString();
  await client.sitemapPartProgress.upsert({
    where: { loc },
    create: { loc, lastmod, completedAt },
    update: { lastmod, completedAt },
  });
}
