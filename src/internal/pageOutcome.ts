import type { CrawlResult } from '@just-every/crawl';

/**
 * The explicit outcome for a single page inside a (possibly multi-page) fetch.
 *
 * Keeping the categories explicit lets callers tell a page that was never
 * allowed to be fetched (robots.txt) apart from one that failed while being
 * fetched (timeouts, non-HTML responses, HTTP errors and redirect failures)
 * and from one that was fetched but had no usable article body.
 */
export type PageOutcomeCategory =
    | 'success'
    | 'blocked-by-robots'
    | 'fetch-error'
    | 'content-unavailable';

export type PageFailureCategory = Exclude<PageOutcomeCategory, 'success'>;

/** Per-page outcome exposed on multi-page `FetchMarkdownResult`s. */
export interface PageOutcome {
    /** The URL the page ended up at (the final URL when redirects occurred). */
    url: string;
    /** The URL that was queued for this page, when it differs from `url`. */
    requestedUrl?: string;
    category: PageOutcomeCategory;
    /** Failure reason. Omitted for successful pages. */
    error?: string;
    title?: string;
}

/**
 * Crawl results produced by `secureCrawl` carry a structured failure category
 * so callers do not have to parse error message strings.
 */
export interface CategorizedCrawlResult extends CrawlResult {
    failureCategory?: PageFailureCategory;
    requestedUrl?: string;
}

export const FAILURE_CATEGORY_LABELS: Record<PageFailureCategory, string> = {
    'blocked-by-robots': 'blocked by robots.txt',
    'fetch-error': 'fetch failed',
    'content-unavailable': 'content unavailable',
};

const FAILURE_CATEGORY_SUMMARY_LABELS: Record<
    PageFailureCategory,
    { singular: string; plural: string }
> = {
    'blocked-by-robots': {
        singular: 'blocked by robots.txt',
        plural: 'blocked by robots.txt',
    },
    'fetch-error': { singular: 'fetch error', plural: 'fetch errors' },
    'content-unavailable': {
        singular: 'content unavailable',
        plural: 'content unavailable',
    },
};

/** Categories in the order they are reported in summaries. */
export const FAILURE_CATEGORY_ORDER: PageFailureCategory[] = [
    'blocked-by-robots',
    'fetch-error',
    'content-unavailable',
];

const DEFAULT_FAILURE_REASONS: Record<PageFailureCategory, string> = {
    'blocked-by-robots': 'Blocked by robots.txt',
    'fetch-error': 'Failed to fetch page',
    'content-unavailable': 'No usable content could be extracted',
};

export function defaultFailureReason(category: PageFailureCategory): string {
    return DEFAULT_FAILURE_REASONS[category];
}

/**
 * Map a fetch/extraction error message onto one of the failure categories.
 *
 * `secureCrawl` labels its own results directly; this classifier covers
 * errors that surface elsewhere (URL policy rejections, mocked crawlers).
 */
export function classifyErrorMessage(message: string): PageFailureCategory {
    const lower = message.toLowerCase();

    if (lower.includes('robots')) {
        return 'blocked-by-robots';
    }

    if (
        lower.includes('non-html') ||
        lower.includes('content-type') ||
        lower.includes('timeout') ||
        lower.includes('timed out') ||
        lower.includes('abort') ||
        lower.includes('http ') ||
        lower.includes('network') ||
        lower.includes('socket') ||
        lower.includes('econn') ||
        lower.includes('enotfound') ||
        lower.includes('redirect') ||
        lower.includes('fetch failed')
    ) {
        return 'fetch-error';
    }

    if (
        lower.includes('extract') ||
        lower.includes('limited content') ||
        lower.includes('empty response') ||
        lower.includes('no markdown')
    ) {
        return 'content-unavailable';
    }

    return 'fetch-error';
}

/**
 * Classify a crawl result.
 *
 * `secureCrawl` sets `failureCategory` directly. The message-based fallback
 * keeps results from older/mocked crawlers working.
 */
export function categorizeCrawlResult(
    result: CategorizedCrawlResult
): PageOutcomeCategory {
    if (result.failureCategory) {
        return result.failureCategory;
    }

    const message = result.error;
    if (!message) {
        return 'success';
    }

    return classifyErrorMessage(message);
}

/** Convert a crawl result into the public per-page outcome shape. */
export function toPageOutcome(
    result: CategorizedCrawlResult,
    requestedUrl?: string
): PageOutcome {
    const category = categorizeCrawlResult(result);
    const url = result.url || requestedUrl || '';
    const requested = result.requestedUrl ?? requestedUrl;
    const outcome: PageOutcome = { url, category };

    if (requested && requested !== url) {
        outcome.requestedUrl = requested;
    }
    if (category !== 'success') {
        outcome.error = result.error || defaultFailureReason(category);
    }
    if (result.title) {
        outcome.title = result.title;
    }

    return outcome;
}

/** Summarize failures by count and category (never by listing URLs). */
export function summarizePageFailures(
    pages: PageOutcome[]
): string | undefined {
    const failed = pages.filter(page => page.category !== 'success');
    if (failed.length === 0) {
        return undefined;
    }

    const counts = new Map<PageFailureCategory, number>();
    for (const page of failed) {
        const category = page.category as PageFailureCategory;
        counts.set(category, (counts.get(category) ?? 0) + 1);
    }

    const parts = FAILURE_CATEGORY_ORDER.filter(category =>
        counts.has(category)
    ).map(category => {
        const count = counts.get(category) as number;
        const labels = FAILURE_CATEGORY_SUMMARY_LABELS[category];
        return `${count} ${count === 1 ? labels.singular : labels.plural}`;
    });

    const total = pages.length;
    const pageWord = total === 1 ? 'page' : 'pages';
    return `${failed.length} of ${total} ${pageWord} failed: ${parts.join(', ')}`;
}
