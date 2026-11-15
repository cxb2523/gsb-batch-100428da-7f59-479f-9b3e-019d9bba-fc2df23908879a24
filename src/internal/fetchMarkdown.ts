import type { CrawlOptions } from '@just-every/crawl';
import {
    extractMarkdownLinks,
    filterSameOriginLinks,
} from '../utils/extractMarkdownLinks.js';
import { assertPublicHttpUrl } from '../utils/urlPolicy.js';
import { secureCrawl } from './secureCrawl.js';
import { getErrorFinalUrl } from './secureFetchHtml.js';
import {
    FAILURE_CATEGORY_LABELS,
    summarizePageFailures,
    toPageOutcome,
    type CategorizedCrawlResult,
    type PageFailureCategory,
    type PageOutcome,
} from './pageOutcome.js';

export interface FetchMarkdownOptions {
    depth?: number;
    maxConcurrency?: number;
    respectRobots?: boolean;
    sameOriginOnly?: boolean;
    userAgent?: string;
    cacheDir?: string;
    timeout?: number;
    maxPages?: number;
    cookiesFile?: string;
}

export interface FetchMarkdownResult {
    markdown: string;
    title?: string;
    links?: string[];
    error?: string;
    /**
     * Per-page outcomes, in crawl order. Every page that was attempted has
     * exactly one entry recording its final URL and outcome category, so a
     * multi-page fetch can be checked page by page even when some pages failed.
     */
    pages?: PageOutcome[];
}

/** A queued page together with the outcome of processing it. */
interface ProcessedPage {
    outcome: PageOutcome;
    result?: CategorizedCrawlResult;
}

export async function fetchMarkdown(
    url: string,
    options: FetchMarkdownOptions = {}
): Promise<FetchMarkdownResult> {
    const maxPages = Math.max(1, options.maxPages ?? 1);

    if (maxPages === 1) {
        return fetchSinglePage(url, options);
    }

    return fetchMultiplePages(url, options, maxPages);
}

/**
 * Single-page fetch.
 *
 * Kept compatible with the previous behaviour that CLI and MCP callers depend
 * on: a URL-policy rejection surfaces as a top-level error with empty markdown
 * (callers treat that as a hard failure), a crawl failure is reported inside
 * the markdown/error pair using the historical message, and a successful page
 * produces the same markdown. The only addition is the `pages` outcome record.
 */
async function fetchSinglePage(
    url: string,
    options: FetchMarkdownOptions
): Promise<FetchMarkdownResult> {
    try {
        await assertPublicHttpUrl(url);

        const results = await secureCrawl(url, buildCrawlOptions(options));

        if (results.length === 0) {
            return {
                markdown: '',
                error: 'No results returned',
                pages: [],
            };
        }

        const result = results[0];
        const outcome = toPageOutcome(result, url);
        let combinedMarkdown: string;

        if (result.error) {
            combinedMarkdown = `<!-- Error fetching ${sanitizeComment(result.url)}: ${sanitizeComment(result.error)} -->`;
        } else {
            combinedMarkdown =
                `<!-- Source: ${sanitizeComment(result.url)} -->\n` +
                (result.markdown || '');
        }

        return {
            markdown: combinedMarkdown,
            title: outcome.title,
            links: result.links || [],
            error: result.error
                ? `Some pages had errors: ${result.url}`
                : undefined,
            pages: [outcome],
        };
    } catch (error) {
        return {
            markdown: '',
            error: error instanceof Error ? error.message : 'Unknown error',
            pages: [],
        };
    }
}

/**
 * Multi-page fetch.
 *
 * Every queued page records its own outcome. A page failing (robots rejection,
 * fetch exception such as a timeout or non-HTML response, or unusable article
 * body) never aborts the remaining queue: the loop keeps dequeuing links until
 * `maxPages` pages have been attempted or the queue is exhausted.
 */
async function fetchMultiplePages(
    url: string,
    options: FetchMarkdownOptions,
    maxPages: number
): Promise<FetchMarkdownResult> {
    try {
        const visited = new Set<string>();
        const toVisit = [url];
        const processed: ProcessedPage[] = [];

        while (toVisit.length > 0 && processed.length < maxPages) {
            const currentUrl = toVisit.shift()!;

            // Skip if already visited
            if (visited.has(currentUrl)) continue;
            visited.add(currentUrl);

            const page = await processSinglePage(currentUrl, options);
            processed.push(page);

            // Extract links from any page that produced markdown (including
            // partially extracted pages) so remaining slots can still fill.
            if (processed.length < maxPages && page.result?.markdown) {
                const baseUrl = page.outcome.url || currentUrl;
                const links = extractMarkdownLinks(
                    page.result.markdown,
                    baseUrl
                );
                const filteredLinks =
                    options.sameOriginOnly !== false
                        ? filterSameOriginLinks(links, baseUrl)
                        : links;

                for (const link of filteredLinks) {
                    if (!visited.has(link) && !toVisit.includes(link)) {
                        toVisit.push(link);
                    }
                }
            }
        }

        if (processed.length === 0) {
            return {
                markdown: '',
                error: 'No results returned',
                pages: [],
            };
        }

        const pages = processed.map(page => page.outcome);

        // Combine pages in crawl order. Successful pages keep their original
        // order and separators; failed pages keep their slot so readers can
        // see exactly which page is missing and why.
        const combinedMarkdown = processed
            .map((page, index) => {
                let block = '';

                if (processed.length > 1 && index > 0) {
                    block += '\n\n---\n\n';
                }

                block += renderPageBlock(page);
                return block;
            })
            .join('\n');

        const firstSuccessful = processed.find(
            page => page.outcome.category === 'success'
        );

        return {
            markdown: combinedMarkdown,
            title: firstSuccessful?.outcome.title ?? processed[0].outcome.title,
            links: processed.flatMap(page => page.result?.links || []),
            error: summarizePageFailures(pages),
            pages,
        };
    } catch (error) {
        return {
            markdown: '',
            error: error instanceof Error ? error.message : 'Unknown error',
            pages: [],
        };
    }
}

/**
 * Fetch one page, converting any failure into a recorded outcome instead of
 * throwing so the caller can continue with the rest of the queue.
 */
async function processSinglePage(
    currentUrl: string,
    options: FetchMarkdownOptions
): Promise<ProcessedPage> {
    try {
        await assertPublicHttpUrl(currentUrl);
        const results = await secureCrawl(
            currentUrl,
            buildCrawlOptions(options)
        );

        if (results.length > 0) {
            const result = results[0];
            return {
                result,
                outcome: toPageOutcome(result, currentUrl),
            };
        }
    } catch (error) {
        return {
            result: {
                url: getErrorFinalUrl(error) ?? currentUrl,
                markdown: '',
                error: error instanceof Error ? error.message : 'Unknown error',
                failureCategory: 'fetch-error',
                requestedUrl: currentUrl,
            },
            outcome: {
                url: getErrorFinalUrl(error) ?? currentUrl,
                requestedUrl: currentUrl,
                category: 'fetch-error',
                error: error instanceof Error ? error.message : 'Unknown error',
            },
        };
    }

    return {
        result: {
            url: currentUrl,
            markdown: '',
            error: 'No result returned for this page',
            failureCategory: 'fetch-error',
            requestedUrl: currentUrl,
        },
        outcome: {
            url: currentUrl,
            category: 'fetch-error',
            error: 'No result returned for this page',
        },
    };
}

function buildCrawlOptions(options: FetchMarkdownOptions): CrawlOptions {
    const crawlOptions: CrawlOptions = {
        depth: 0, // Always single page
        maxConcurrency: options.maxConcurrency ?? 3,
        respectRobots: options.respectRobots ?? true,
        sameOriginOnly: options.sameOriginOnly ?? true,
        userAgent: options.userAgent,
        cacheDir: options.cacheDir ?? '.cache',
        timeout: options.timeout ?? 30000,
    };
    if (options.cookiesFile) {
        (crawlOptions as any).cookiesFile = options.cookiesFile;
    }

    return crawlOptions;
}

function renderPageBlock(page: ProcessedPage): string {
    const { outcome } = page;

    if (outcome.category === 'success') {
        let content = `<!-- Source: ${sanitizeComment(outcome.url)} -->\n`;
        content += page.result?.markdown || '';
        return content;
    }

    const category = outcome.category as PageFailureCategory;
    const categoryLabel = FAILURE_CATEGORY_LABELS[category];
    const reason = outcome.error || 'Unknown error';
    const requested =
        outcome.requestedUrl && outcome.requestedUrl !== outcome.url
            ? ` (requested: ${sanitizeComment(outcome.requestedUrl)})`
            : '';

    let block =
        `<!-- Source: ${sanitizeComment(outcome.url)}${requested} -->\n` +
        `<!-- Failed page: ${categoryLabel} (${category}) - ${sanitizeComment(reason)} -->`;

    // A page can fail article extraction yet still yield partial content
    // (e.g. JavaScript-rendered pages). Keep that content usable.
    const partialMarkdown = page.result?.markdown;
    if (partialMarkdown && partialMarkdown.trim()) {
        block += `\n${partialMarkdown}`;
    }

    return block;
}

function sanitizeComment(value: string): string {
    // Prevent accidental termination of the surrounding HTML comment.
    return value.replace(/--/g, '- -').replace(/\n/g, ' ');
}

export type { PageFailureCategory, PageOutcome };
