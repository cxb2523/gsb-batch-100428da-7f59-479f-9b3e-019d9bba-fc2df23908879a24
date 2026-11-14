import { Actor } from 'apify';
import { type CheerioAPI, gotScraping, log, type ProxyConfiguration } from 'crawlee';
import type { Element } from 'domhandler';

import type { OrganicResult, SearchResultType } from '../types.js';

// Google's click-tracking redirect endpoints, sometimes served instead of a plain result anchor.
// Relative to the page origin; resolved to the real destination by resolveGoogleRedirectUrls.
const GOOGLE_REDIRECT_PATHNAMES = new Set(['/goto', '/url']);

// Searches always run on www.google.com (see createSearchRequest), so redirect links point there too
const GOOGLE_HOSTNAME = 'www.google.com';
const GOOGLE_ORIGIN = `https://${GOOGLE_HOSTNAME}`;

const MAX_RESOLVE_ATTEMPTS = 3;

/** Why one attempt or one redirect entry failed to resolve. */
export type RedirectFailureReason =
    /** The response was not a 3xx redirect, so it carries no destination. */
    | 'no-redirect-status'
    /** The redirect had no usable Location header. */
    | 'missing-location'
    /** The Location was unparseable, not an http(s) result page, or another Google interstitial. */
    | 'invalid-location'
    /** The request itself failed (network, timeout, proxy, ...). */
    | 'request-failed'
    /** The attempts failed for different reasons, so no single reason describes the entry. */
    | 'all-attempts-failed';

export type RedirectAttemptFailure = {
    reason: RedirectFailureReason;
    message: string;
};

/** One result dropped because its Google redirect link could not be resolved to a real destination. */
export type DroppedRedirect = {
    /** The link as extracted from the SERP, before any resolution. */
    originalUrl: string;
    reason: RedirectFailureReason;
    message: string;
    /** One entry per attempt, so a partial failure is still visible. */
    attempts: RedirectAttemptFailure[];
};

/**
 * What happened while resolving Google redirect links on one page of results.
 * Counts make a page's losses checkable instead of just showing fewer results.
 */
export type RedirectResolutionSummary = {
    /** Results handed to the resolver. */
    totalResults: number;
    /** Results that were not Google redirect links; kept as-is without a request. */
    untouched: number;
    /** Google redirect links that entered resolution. */
    enteredResolution: number;
    /** Entries replaced by their real destination (local parameter hits plus resolved requests). */
    resolved: number;
    /** Destinations read from the /url q or url parameter without asking Google. */
    localParamHits: number;
    /** Destinations obtained by reading a redirect Location header from Google. */
    resolvedByRequest: number;
    /** Entries dropped because no destination could be found. */
    dropped: number;
    droppedRedirects: DroppedRedirect[];
};

export type RedirectResolution = {
    results: OrganicResult[];
    summary: RedirectResolutionSummary;
};

function isGoogleRedirectPath(href: string): boolean {
    return GOOGLE_REDIRECT_PATHNAMES.has(href.split(/[?#]/)[0]);
}

/** Google pages a redirect can lead to that are not results: consent, captcha or another redirect hop. */
function isGoogleInterstitial(url: URL): boolean {
    if (url.hostname === 'consent.google.com') {
        return true;
    }
    return url.hostname === GOOGLE_HOSTNAME && (url.pathname.startsWith('/sorry') || GOOGLE_REDIRECT_PATHNAMES.has(url.pathname));
}

/** Validates that a URL is absolute http/https and not one of Google's own internal search links. */
function isValidUrl(url: string): boolean {
    if (!url || typeof url !== 'string') {
        return false;
    }

    try {
        const urlObj = new URL(url);
        if ((urlObj.hostname === 'google.com' || urlObj.hostname.endsWith('.google.com')) && urlObj.pathname.startsWith('/search')) {
            return false;
        }
        return urlObj.protocol === 'http:' || urlObj.protocol === 'https:';
    } catch {
        return false;
    }
}

/**
 * Deduplicates search results by URL; a page listed under different titles is still one page.
 */
export const deduplicateResults = <T extends { url?: string }>(results: T[]): T[] => {
    const uniqueResults: T[] = [];
    const seenUrls = new Set<string | undefined>();
    for (const result of results) {
        if (seenUrls.has(result.url)) continue;
        seenUrls.add(result.url);
        uniqueResults.push(result);
    }
    return uniqueResults;
};

/**
 * Parses a single organic search result (source: @apify/google-search).
 */
const parseResult = ($: CheerioAPI, el: Element) => {
    $(el).find('div.action-menu').remove();

    const descriptionSelector = '.VwiC3b';
    const href = $(el).find('a').first().attr('href') || '';
    const searchResult: OrganicResult = {
        title: $(el).find('h3').first().text() || '',
        description: ($(el).find(descriptionSelector).text() || '').trim(),
        url: href && isGoogleRedirectPath(href) ? new URL(href, GOOGLE_ORIGIN).href : href,
    };

    return searchResult;
};

/**
 * Extracts search results from the given selectors (source: @apify/google-search).
 */
const extractResultsFromSelectors = ($: CheerioAPI, selectors: string[]) => {
    const searchResults: OrganicResult[] = [];
    const selector = selectors.join(', ');
    for (const resultEl of $(selector)) {
        const results = $(resultEl).map((_i, el) => parseResult($, el as Element)).toArray();
        for (const result of results) {
            // Only include results with both title and a valid URL
            // URL validation filters out Google's internal search links and malformed URLs
            // that would cause errors during content crawling
            if (result.title && result.url && isValidUrl(result.url)) {
                searchResults.push(result);
            }
        }
    }
    return searchResults;
};

/**
 * If true, the results are not inherent to the given query, but to a similar suggested query
 */
const areTheResultsSuggestions = ($: CheerioAPI) => {
    // Check if the message "No results found" is shown
    return $('div#topstuff > div.fSp71d').children().length > 0;
};

/**
 * Extracts organic search results from the given Cheerio instance (source: @apify/google-search).
 */
export const scrapeOrganicResults = ($: CheerioAPI): OrganicResult[] => {
    const resultSelectors2023January = [
        '.hlcw0c', // Top result with site links
        '.g.Ww4FFb', // General search results
        '.MjjYud', // General search results 2025 March, this includes also images so we need to add a check that results has both title and url
        '.g .tF2Cxc>.yuRUbf', // old search selector 2021 January
        '.g [data-header-feature="0"]', // old search selector 2022 January
        '.g .rc', // very old selector
        '.sATSHe', // another new selector in March 2025
    ];

    const searchResults = extractResultsFromSelectors($, resultSelectors2023January);
    const deduplicatedResults = deduplicateResults(searchResults);
    let resultType: SearchResultType = 'ORGANIC';
    if (areTheResultsSuggestions($)) {
        resultType = 'SUGGESTED';
    }
    return deduplicatedResults.map((result) => ({
        ...result,
        resultType,
    }));
};

const parseGoogleRedirectUrl = (url: string): URL | null => {
    let parsedUrl: URL;
    try {
        parsedUrl = new URL(url);
    } catch {
        return null;
    }
    const isHttp = parsedUrl.protocol === 'http:' || parsedUrl.protocol === 'https:';
    if (!isHttp || parsedUrl.hostname !== GOOGLE_HOSTNAME || !GOOGLE_REDIRECT_PATHNAMES.has(parsedUrl.pathname)) {
        return null;
    }
    return parsedUrl;
};

/** /url links carry their destination in the q (or url) parameter, so it can be read without asking Google. */
const readUrlDestination = (parsedUrl: URL): string | null => {
    const param = parsedUrl.searchParams.get('q') ?? parsedUrl.searchParams.get('url');
    if (parsedUrl.pathname !== '/url' || !param) {
        return null;
    }
    let destination: URL;
    try {
        destination = new URL(param);
    } catch {
        return null;
    }
    return isValidUrl(destination.href) && !isGoogleInterstitial(destination) ? destination.href : null;
};

/** A failed attempt, carrying the reason so the caller can report it instead of just "failed". */
class RedirectAttemptError extends Error {
    readonly reason: RedirectFailureReason;

    constructor(reason: RedirectFailureReason, message: string) {
        super(message);
        this.name = 'RedirectAttemptError';
        this.reason = reason;
    }
}

const resolveGoogleRedirectUrl = async (parsedUrl: URL, proxyConfig: ProxyConfiguration | undefined): Promise<string> => {
    let response;
    try {
        response = await gotScraping({
            url: parsedUrl.toString(),
            followRedirect: false,
            proxyUrl: await proxyConfig?.newUrl(),
            timeout: { request: 5_000 },
        });
    } catch (error) {
        throw new RedirectAttemptError('request-failed', `${error instanceof Error ? error.message : String(error)}`);
    }
    const statusCode = response?.statusCode;
    const location: unknown = response?.headers?.location;
    if (statusCode < 300 || statusCode >= 400) {
        throw new RedirectAttemptError('no-redirect-status', `response is not a redirect (status ${statusCode})`);
    }
    if (typeof location !== 'string' || !location) {
        throw new RedirectAttemptError('missing-location', `redirect has no Location header (status ${statusCode})`);
    }
    // Google's own pages (captcha, consent) also come as redirects; failing here keeps them from winning the race
    let destination: URL;
    try {
        destination = new URL(location, parsedUrl);
    } catch {
        throw new RedirectAttemptError('invalid-location', `Location is not a url: ${location}`);
    }
    if (!isValidUrl(destination.href) || isGoogleInterstitial(destination)) {
        throw new RedirectAttemptError('invalid-location', `redirect does not lead to a result page: ${destination.href}`);
    }
    return destination.href;
};

/** Describes why resolving one redirect entry failed, preferring a single attempt's reason when they agree. */
function describeFailedAttempts(attempts: RedirectAttemptFailure[], fallbackMessage: string): Pick<DroppedRedirect, 'reason' | 'message'> {
    const reasons = new Set(attempts.map((attempt) => attempt.reason));
    if (reasons.size === 1) {
        return { reason: attempts[0].reason, message: attempts[0].message };
    }
    const details = attempts.map((attempt) => `${attempt.reason}: ${attempt.message}`).join('; ');
    return { reason: 'all-attempts-failed', message: details || fallbackMessage };
}

type ResolvedEntry = {
    result: OrganicResult;
    /** How the entry was resolved, or undefined when it was not a redirect link. */
    outcome: 'untouched' | 'local' | 'request' | 'dropped';
    dropped?: DroppedRedirect;
};

async function resolveEntry(result: OrganicResult, proxyConfig: ProxyConfiguration | undefined): Promise<ResolvedEntry> {
    const parsedUrl = result.url && parseGoogleRedirectUrl(result.url);
    if (!parsedUrl) {
        return { result, outcome: 'untouched' };
    }

    const urlDestination = readUrlDestination(parsedUrl);
    if (urlDestination) {
        return { result: { ...result, url: urlDestination }, outcome: 'local' };
    }

    // Attempts run in parallel, each through a different proxy IP, and the first to resolve wins
    const attempts = Array.from({ length: MAX_RESOLVE_ATTEMPTS }, async () => resolveGoogleRedirectUrl(parsedUrl, proxyConfig));
    try {
        const destination = await Promise.any(attempts);
        return { result: { ...result, url: destination }, outcome: 'request' };
    } catch (error) {
        // Promise.any rejects with an AggregateError once every attempt has failed
        const attemptFailures: RedirectAttemptFailure[] = (error as AggregateError).errors.map((attemptError) => ({
            reason: attemptError instanceof RedirectAttemptError ? attemptError.reason : 'request-failed',
            message: attemptError instanceof RedirectAttemptError ? attemptError.message : `${attemptError}`,
        }));
        const failure = describeFailedAttempts(attemptFailures, `${error}`);
        return {
            result,
            outcome: 'dropped',
            dropped: { originalUrl: result.url!, ...failure, attempts: attemptFailures },
        };
    }
}

/**
 * Replaces Google's redirect urls (/goto and /url) with their destination.
 * /url links are read locally; /goto links (and /url without a usable destination) from Google's redirect response.
 * Anything that cannot be resolved is dropped, and reported in the returned summary.
 */
export const resolveGoogleRedirectUrlsWithSummary = async (results: OrganicResult[]): Promise<RedirectResolution> => {
    // Not needed when nothing has to go through Google, so a page without /goto links stays request-free
    const needsProxy = results.some((result) => {
        const parsedUrl = result.url && parseGoogleRedirectUrl(result.url);
        return Boolean(parsedUrl && !readUrlDestination(parsedUrl));
    });
    const proxyConfig = needsProxy ? await Actor.createProxyConfiguration({ checkAccess: false }) : undefined;

    const entries = await Promise.all(results.map(async (result) => resolveEntry(result, proxyConfig)));

    const summary: RedirectResolutionSummary = {
        totalResults: results.length,
        untouched: 0,
        enteredResolution: 0,
        resolved: 0,
        localParamHits: 0,
        resolvedByRequest: 0,
        dropped: 0,
        droppedRedirects: [],
    };
    const resolvedResults: OrganicResult[] = [];
    for (const entry of entries) {
        if (entry.outcome === 'untouched') {
            summary.untouched += 1;
            resolvedResults.push(entry.result);
            continue;
        }
        summary.enteredResolution += 1;
        if (entry.outcome === 'dropped') {
            summary.dropped += 1;
            summary.droppedRedirects.push(entry.dropped!);
            continue;
        }
        summary.resolved += 1;
        if (entry.outcome === 'local') summary.localParamHits += 1;
        else summary.resolvedByRequest += 1;
        resolvedResults.push(entry.result);
    }

    if (summary.dropped > 0) {
        log.warning('Dropped search results whose Google redirect url could not be resolved', {
            dropped: summary.dropped,
            droppedRedirects: summary.droppedRedirects.map(({ originalUrl, reason, message }) => ({ originalUrl, reason, message })),
        });
    }

    return { results: resolvedResults, summary };
};

/**
 * Replaces Google's redirect urls (/goto and /url) with their destination.
 * Thin wrapper over {@link resolveGoogleRedirectUrlsWithSummary} for callers that only need the results.
 */
export const resolveGoogleRedirectUrls = async (results: OrganicResult[]): Promise<OrganicResult[]> => {
    const { results: resolvedResults } = await resolveGoogleRedirectUrlsWithSummary(results);
    return resolvedResults;
};
