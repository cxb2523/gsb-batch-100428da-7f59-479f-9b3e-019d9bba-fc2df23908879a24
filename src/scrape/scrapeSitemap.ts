import type { Prisma } from '@prisma/client';
import { prisma } from '..';
import {
  IGNORED_WAF_TAGS,
  SITEMAP_INDEX_URL,
  SITEMAP_PROGRESS_CATEGORY,
} from '../constants';
import {
  CloudflareError,
  FetchTimeoutError,
  HttpError,
  fetchErrorCategory,
  isTransientFetchError,
} from '../fetch/errors';
import { fetchURL } from '../fetch/fetchURL';
import {
  getCategoryScraped,
  updateCategoryScraped,
} from '../helpers/lastScrapedHandler';
import {
  getCompletedSitemapParts,
  markSitemapPartCompleted,
} from '../helpers/sitemapPartProgress';
import {
  isJapaneseSitemapPart,
  lastmodToEpochMs,
  lastmodToUpdatedAt,
  parseSitemapIndex,
  parseUrlset,
  SitemapSchemaError,
  tagNameFromArticleUrl,
  type SitemapEntry,
  type SitemapPartCompletion,
  type SitemapPartFailure,
  type SitemapSweepSummary,
} from '../helpers/sitemap';

const UPSERT_BATCH_SIZE = 50;
// A whole part is written in one transaction so a failed part leaves no
// half-applied articles; keep that transaction from hitting the short default.
const PART_TRANSACTION_TIMEOUT_MS = 120_000;

export type {
  SitemapPartCompletion,
  SitemapPartFailure,
  SitemapSweepSummary,
} from '../helpers/sitemap';

/**
 * Discovers Japanese sitemap parts and scrapes the ones that are not yet
 * recorded as complete at their current index lastmod.
 *
 * A single part failure no longer abandons the round: the remaining parts are
 * still processed and every outcome is reported. Completed parts are recorded
 * individually (in the same transaction as their articles), so a rerun selects
 * exactly the parts that did not finish. If any part failed, the first
 * underlying error is rethrown after the round summary, preserving the
 * existing error types (e.g. `SitemapSchemaError` for malformed XML).
 */
export async function scrapeSitemap(
  signal?: AbortSignal,
): Promise<SitemapSweepSummary> {
  signal?.throwIfAborted();
  const removed = await prisma.pixivArticle.deleteMany({
    where: { tag_name: { in: [...IGNORED_WAF_TAGS] } },
  });
  if (removed.count > 0) {
    console.log(`Removed ${removed.count} known WAF sitemap entries`);
  }

  signal?.throwIfAborted();
  const indexResponse = await fetchURL(SITEMAP_INDEX_URL);
  signal?.throwIfAborted();
  if (typeof indexResponse.data !== 'string') {
    throw new Error('Sitemap index was not XML text');
  }

  const parts = parseSitemapIndex(indexResponse.data)
    .filter((part) => isJapaneseSitemapPart(part.loc))
    .sort((a, b) => lastmodToEpochMs(b.lastmod) - lastmodToEpochMs(a.lastmod));

  const completedParts = await seedCompletedSitemapParts(parts);
  const dueParts = parts.filter((part) => {
    const completedLastmod = completedParts.get(part.loc);
    // A part never recorded as complete is always due.
    if (completedLastmod === undefined) return true;
    return lastmodToEpochMs(part.lastmod) > lastmodToEpochMs(completedLastmod);
  });

  console.log(
    `Sitemap: ${dueParts.length}/${parts.length} parts to scrape (${parts.length - dueParts.length} already complete)`,
  );

  const completed: SitemapPartCompletion[] = [];
  const failed: SitemapPartFailure[] = [];
  const failures: unknown[] = [];

  let partIndex = 0;
  for (const part of dueParts) {
    partIndex++;
    signal?.throwIfAborted();
    try {
      const upserted = await scrapeSitemapPart(part, signal);
      completed.push({ loc: part.loc, lastmod: part.lastmod, upserted });
      console.log(
        `Sitemap: ${part.loc} ${partIndex}/${dueParts.length} completed (${upserted} upserted)`,
      );
    } catch (error) {
      // A requested shutdown is not a part-level failure: stop the round and
      // let the caller report the interruption.
      if (signal?.aborted) signal.throwIfAborted();
      const reason = sitemapPartFailureReason(error);
      failed.push({ loc: part.loc, lastmod: part.lastmod, reason });
      failures.push(error);
      console.error(
        `Sitemap: ${part.loc} ${partIndex}/${dueParts.length} failed (${reason}); continuing with remaining parts`,
      );
    }
  }

  signal?.throwIfAborted();

  const summary: SitemapSweepSummary = {
    total: dueParts.length,
    completed,
    failed,
  };
  reportSitemapSweepSummary(summary);

  if (failures.length > 0) {
    const primary = failures[0];
    if (primary instanceof Error) {
      (primary as SitemapSweepFailure).sitemapSweep = summary;
      throw primary;
    }
    throw new Error(`Sitemap: ${failed.length} part(s) failed`);
  }

  // The legacy category watermark is a coarse "last fully successful round"
  // marker. It advances only when every due part finished, and resume selection
  // never reads it, so a failed part cannot be skipped by a stale value.
  signal?.throwIfAborted();
  if (parts.length > 0) {
    await updateCategoryScraped({
      category: SITEMAP_PROGRESS_CATEGORY,
      date: parts[0].lastmod,
      sort: 'newest',
    });
  }

  return summary;
}

export type SitemapSweepFailure = Error & {
  sitemapSweep: SitemapSweepSummary;
};

/**
 * Reads the per-part completion ledger, seeding it from the legacy category
 * watermark when it is still empty. Existing databases already recorded a
 * fully successful round in `scrapeProgress`, so those parts must not all
 * become due again merely because the ledger is new.
 */
async function seedCompletedSitemapParts(
  parts: SitemapEntry[],
): Promise<Map<string, string>> {
  const completed = await getCompletedSitemapParts();
  if (completed.size > 0) return completed;

  const lastScrape = await getCategoryScraped({
    category: SITEMAP_PROGRESS_CATEGORY,
    sort: 'newest',
  });
  const watermarkMs = new Date(lastScrape).getTime();
  if (!Number.isFinite(watermarkMs)) return completed;

  const previouslyCompleted = parts.filter(
    (part) => lastmodToEpochMs(part.lastmod) <= watermarkMs,
  );
  if (previouslyCompleted.length === 0) return completed;

  await prisma.$transaction(async (tx) => {
    for (const part of previouslyCompleted) {
      await markSitemapPartCompleted(tx, part.loc, part.lastmod);
    }
  });
  const seeded = new Map(
    previouslyCompleted.map((part) => [part.loc, part.lastmod]),
  );
  console.log(
    `Sitemap: seeded ${seeded.size} completed parts from the previous watermark`,
  );
  return seeded;
}

/** Reads the round summary attached to the error a failed sweep throws. */
export function sitemapSweepFromError(
  error: unknown,
): SitemapSweepSummary | undefined {
  return (error as Partial<SitemapSweepFailure> | null)?.sitemapSweep;
}

/**
 * Classifies a failed part into a stable, bounded reason for the round summary.
 * Detail is sanitized (no URLs/response bodies) and truncated so a report stays
 * safe and small while still identifying what went wrong.
 */
export function sitemapPartFailureReason(error: unknown): string {
  if (error instanceof SitemapSchemaError) {
    return `parse-failed (${sanitizeFailureDetail(error.message)})`;
  }
  if (error instanceof CloudflareError) {
    return 'fetch-blocked (cloudflare challenge)';
  }
  if (error instanceof HttpError) {
    return `fetch-failed (HTTP ${error.status})`;
  }
  if (error instanceof FetchTimeoutError) {
    return error.budgetExhausted
      ? 'fetch-timeout (budget exhausted)'
      : 'fetch-timeout';
  }
  if (isTransientFetchError(error)) {
    return `fetch-failed (${fetchErrorCategory(error)})`;
  }
  if (error instanceof Error) {
    return `error (${error.name}: ${sanitizeFailureDetail(error.message)})`;
  }
  return 'error (non-error thrown)';
}

function sanitizeFailureDetail(message: string): string {
  const sanitized = message
    .replace(/\b(?:https?|wss?):\/\/\S+/gi, '<url>')
    .replace(/\s+/g, ' ')
    .trim();
  return sanitized.length > 200 ? `${sanitized.slice(0, 197)}...` : sanitized;
}

function reportSitemapSweepSummary(summary: SitemapSweepSummary) {
  console.log(
    `Sitemap round summary: ${summary.completed.length} completed, ${summary.failed.length} failed (${summary.total} due)`,
  );
  for (const part of summary.completed) {
    console.log(
      `Sitemap completed part: ${part.loc} lastmod=${part.lastmod} upserted=${part.upserted}`,
    );
  }
  for (const part of summary.failed) {
    console.log(
      `Sitemap failed part: ${part.loc} lastmod=${part.lastmod} reason=${part.reason}`,
    );
  }
}

async function scrapeSitemapPart(
  part: SitemapEntry,
  signal?: AbortSignal,
): Promise<number> {
  const response = await fetchURL(part.loc);
  signal?.throwIfAborted();
  if (typeof response.data !== 'string') {
    throw new Error(`Sitemap part was not XML text: ${part.loc}`);
  }

  const articles: {
    tag_name: string;
    updated_at: string;
    lastScraped: string;
  }[] = [];
  for (const url of parseUrlset(response.data)) {
    const tag_name = tagNameFromArticleUrl(url.loc);
    if (!tag_name || IGNORED_WAF_TAGS.has(tag_name)) {
      if (tag_name && IGNORED_WAF_TAGS.has(tag_name)) {
        console.log(
          `Ignoring known WAF sitemap entry: ${JSON.stringify(tag_name)}`,
        );
      }
      continue;
    }
    articles.push({
      tag_name,
      updated_at: lastmodToUpdatedAt(url.lastmod),
      lastScraped: lastmodToEpochMs(url.lastmod).toString(),
    });
  }

  signal?.throwIfAborted();
  // All batches of a part, plus its completion row, commit or roll back
  // together. A part that fails mid-write leaves neither partial articles nor
  // a progress entry, so the next run selects it again.
  await prisma.$transaction(
    async (tx) => {
      for (const batch of chunk(articles, UPSERT_BATCH_SIZE)) {
        signal?.throwIfAborted();
        await upsertArticleBatch(tx, batch);
      }
      await markSitemapPartCompleted(tx, part.loc, part.lastmod);
    },
    { timeout: PART_TRANSACTION_TIMEOUT_MS },
  );
  return articles.length;
}

async function upsertArticleBatch(
  client: Pick<Prisma.TransactionClient, '$executeRawUnsafe'>,
  batch: { tag_name: string; updated_at: string; lastScraped: string }[],
) {
  if (batch.length === 0) {
    return;
  }
  const placeholders = batch.map(() => '(?,?,?,?,?,?,?,?,?)').join(',');
  const values = batch.flatMap((article) => [
    article.tag_name,
    '',
    article.updated_at,
    '',
    0,
    0,
    0,
    '[]',
    article.lastScraped,
  ]);
  await client.$executeRawUnsafe(
    `INSERT INTO PixivArticle (tag_name, summary, updated_at, main_illst_url, view_count, illust_count, check_count, related_tags, lastScraped)
     VALUES ${placeholders}
     ON CONFLICT(tag_name) DO UPDATE SET
       updated_at = excluded.updated_at,
       lastScraped = excluded.lastScraped
     WHERE PixivArticle.lastScrapedArticle IS NULL
        OR CAST(PixivArticle.lastScrapedArticle AS INTEGER) < CAST(excluded.lastScraped AS INTEGER)`,
    ...values,
  );
}

function chunk<T>(items: T[], size: number): T[][] {
  const batches: T[][] = [];
  for (let i = 0; i < items.length; i += size) {
    batches.push(items.slice(i, i + size));
  }
  return batches;
}
