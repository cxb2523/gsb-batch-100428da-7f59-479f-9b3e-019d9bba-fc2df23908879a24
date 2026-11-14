import fs from 'node:fs';
import path from 'node:path';

import { Actor } from 'apify';
import { load } from 'cheerio';
import type { CheerioAPI } from 'crawlee';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { resolveGoogleRedirectUrls, resolveGoogleRedirectUrlsWithSummary, scrapeOrganicResults } from '../src/google-search/google-extractors-urls.js';

const { gotScrapingMock } = vi.hoisted(() => ({ gotScrapingMock: vi.fn() }));

vi.mock('crawlee', async (importOriginal) => ({
    ...(await importOriginal<object>()),
    gotScraping: gotScrapingMock,
}));

const resolveUrls = async (urls: string[]) => {
    const results = await resolveGoogleRedirectUrls(urls.map((url) => ({ title: 'title', url })));
    return results.map((result) => result.url);
};

describe('resolving Google redirect urls', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        vi.spyOn(Actor, 'createProxyConfiguration').mockResolvedValue(undefined);
    });

    it('leaves non-redirect urls untouched without requesting them', async () => {
        const urls = ['https://example.com/page', 'https://example.com/goto?url=abc', 'https://www.google.com/maps', 'https://www.google.de/url?q=abc'];

        expect(await resolveUrls(urls)).toEqual(urls);
        expect(gotScrapingMock).not.toHaveBeenCalled();
    });

    it('resolves redirect urls from the location header without following it', async () => {
        gotScrapingMock.mockResolvedValue({ statusCode: 302, headers: { location: 'https://example.com/dest' } });

        expect(await resolveUrls(['https://www.google.com/goto?url=abc'])).toEqual(['https://example.com/dest']);
        expect(gotScrapingMock).toHaveBeenCalledWith(
            expect.objectContaining({ url: 'https://www.google.com/goto?url=abc', followRedirect: false }),
        );
    });

    it('drops the result when no attempt gets a redirect', async () => {
        gotScrapingMock.mockResolvedValue({ statusCode: 403, headers: {} });

        expect(await resolveUrls(['https://www.google.com/goto?url=abc'])).toEqual([]);
        expect(gotScrapingMock).toHaveBeenCalledTimes(3);
    });

    it('keeps the destination of any attempt that succeeds', async () => {
        gotScrapingMock
            .mockRejectedValueOnce(new Error('ECONNREFUSED'))
            .mockResolvedValueOnce({ statusCode: 403, headers: {} })
            .mockResolvedValueOnce({ statusCode: 302, headers: { location: 'https://example.com/dest' } });

        expect(await resolveUrls(['https://www.google.com/goto?url=abc'])).toEqual(['https://example.com/dest']);
        expect(gotScrapingMock).toHaveBeenCalledTimes(3);
    });

    it('reads /url destinations from their parameters without requesting Google', async () => {
        const urls = [
            'https://www.google.com/url?q=https://example.com/from-q&sa=U',
            'https://www.google.com/url?url=https%3A%2F%2Fexample.com%2Ffrom-url',
        ];

        expect(await resolveUrls(urls)).toEqual(['https://example.com/from-q', 'https://example.com/from-url']);
        expect(gotScrapingMock).not.toHaveBeenCalled();
    });

    it('asks Google when the /url parameter is not a valid url', async () => {
        gotScrapingMock.mockResolvedValue({ statusCode: 302, headers: { location: 'https://example.com/dest' } });

        expect(await resolveUrls(['https://www.google.com/url?q=abc', 'https://www.google.com/url?q=https://www.google.com/sorry/index']))
            .toEqual(['https://example.com/dest', 'https://example.com/dest']);
    });

    it('resolves http redirect urls too', async () => {
        gotScrapingMock.mockResolvedValue({ statusCode: 302, headers: { location: 'https://example.com/dest' } });

        expect(await resolveUrls(['http://www.google.com/goto?url=abc'])).toEqual(['https://example.com/dest']);
    });

    it('drops the result when the location comes without a redirect status', async () => {
        gotScrapingMock.mockResolvedValue({ statusCode: 200, headers: { location: 'https://example.com/dest' } });

        expect(await resolveUrls(['https://www.google.com/goto?url=abc'])).toEqual([]);
    });

    it('drops the result when every redirect leads to a captcha or consent page', async () => {
        gotScrapingMock
            .mockResolvedValueOnce({ statusCode: 302, headers: { location: '/sorry/index?continue=abc' } })
            .mockResolvedValueOnce({ statusCode: 302, headers: { location: 'https://consent.google.com/ml?continue=abc' } })
            .mockResolvedValueOnce({ statusCode: 302, headers: { location: 'https://www.google.com/sorry/index' } });

        expect(await resolveUrls(['https://www.google.com/goto?url=abc'])).toEqual([]);
    });

    it('keeps redirects to Google pages that are results', async () => {
        gotScrapingMock.mockResolvedValue({ statusCode: 302, headers: { location: 'https://cloud.google.com/pricing' } });

        expect(await resolveUrls(['https://www.google.com/goto?url=abc'])).toEqual(['https://cloud.google.com/pricing']);
    });

    it('leaves no /goto urls in the results of a SERP', async () => {
        gotScrapingMock.mockImplementation(async ({ url }: { url: string }) => (url.endsWith('resolvable')
            ? { statusCode: 302, headers: { location: 'https://en.wikipedia.org/wiki/TypeScript' } }
            : { statusCode: 403, headers: {} }));
        const html = fs.readFileSync(path.join(__dirname, 'helpers', 'html', 'google-serp-goto.html'), 'utf-8');
        const organicResults = scrapeOrganicResults(load(html) as unknown as CheerioAPI);

        const results = await resolveGoogleRedirectUrls(organicResults);

        expect(results.map(({ title, url }) => ({ title, url }))).toEqual([
            { title: 'Direct result', url: 'https://example.com/direct' },
            { title: 'TypeScript', url: 'https://en.wikipedia.org/wiki/TypeScript' },
        ]);
    });
});

describe('Google redirect resolution summary', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        vi.spyOn(Actor, 'createProxyConfiguration').mockResolvedValue(undefined);
    });

    it('reports a fully successful page without requesting links that need no resolution', async () => {
        const urls = [
            'https://example.com/direct',
            'https://www.google.com/url?q=https://example.com/local',
            'https://www.google.com/goto?url=abc',
        ];
        gotScrapingMock.mockResolvedValue({ statusCode: 302, headers: { location: 'https://example.com/via-request' } });

        const { results, summary } = await resolveGoogleRedirectUrlsWithSummary(urls.map((url) => ({ title: 'title', url })));

        expect(results.map((result) => result.url)).toEqual([
            'https://example.com/direct',
            'https://example.com/local',
            'https://example.com/via-request',
        ]);
        expect(summary).toEqual({
            totalResults: 3,
            untouched: 1,
            enteredResolution: 2,
            resolved: 2,
            localParamHits: 1,
            resolvedByRequest: 1,
            dropped: 0,
            droppedRedirects: [],
        });
        expect(gotScrapingMock).toHaveBeenCalledTimes(3);
    });

    it('names each dropped entry when only part of a page fails', async () => {
        const urls = [
            'https://example.com/direct',
            'https://www.google.com/goto?url=ok',
            'https://www.google.com/goto?url=gone',
            'https://www.google.com/url?q=not-a-url',
        ];
        gotScrapingMock.mockImplementation(async ({ url }: { url: string }) => (url.endsWith('ok')
            ? { statusCode: 302, headers: { location: 'https://example.com/via-request' } }
            : { statusCode: 403, headers: {} }));

        const { results, summary } = await resolveGoogleRedirectUrlsWithSummary(urls.map((url) => ({ title: 'title', url })));

        expect(results.map((result) => result.url)).toEqual(['https://example.com/direct', 'https://example.com/via-request']);
        expect(summary.totalResults).toBe(4);
        expect(summary.untouched).toBe(1);
        expect(summary.enteredResolution).toBe(3);
        expect(summary.resolved).toBe(1);
        expect(summary.localParamHits).toBe(0);
        expect(summary.resolvedByRequest).toBe(1);
        expect(summary.dropped).toBe(2);
        // One attempt per try, all three on the same proxy-free request; the reasons are identical
        expect(summary.droppedRedirects).toEqual([
            {
                originalUrl: 'https://www.google.com/goto?url=gone',
                reason: 'no-redirect-status',
                message: 'response is not a redirect (status 403)',
                attempts: expect.arrayContaining([
                    { reason: 'no-redirect-status', message: 'response is not a redirect (status 403)' },
                ]),
            },
            {
                originalUrl: 'https://www.google.com/url?q=not-a-url',
                reason: 'no-redirect-status',
                message: 'response is not a redirect (status 403)',
                attempts: expect.arrayContaining([
                    { reason: 'no-redirect-status', message: 'response is not a redirect (status 403)' },
                ]),
            },
        ]);
        expect(summary.droppedRedirects[0].attempts).toHaveLength(3);
        expect(summary.droppedRedirects[1].attempts).toHaveLength(3);
    });

    it('reports a page where every redirect fails, with one reason per failed attempt', async () => {
        gotScrapingMock
            .mockRejectedValueOnce(new Error('ECONNREFUSED'))
            .mockResolvedValueOnce({ statusCode: 302, headers: { location: '/sorry/index' } })
            .mockResolvedValueOnce({ statusCode: 200, headers: {} });

        const { results, summary } = await resolveGoogleRedirectUrlsWithSummary([{ title: 'title', url: 'https://www.google.com/goto?url=abc' }]);

        expect(results).toEqual([]);
        expect(summary).toEqual({
            totalResults: 1,
            untouched: 0,
            enteredResolution: 1,
            resolved: 0,
            localParamHits: 0,
            resolvedByRequest: 0,
            dropped: 1,
            droppedRedirects: [{
                originalUrl: 'https://www.google.com/goto?url=abc',
                reason: 'all-attempts-failed',
                message: expect.stringContaining('ECONNREFUSED'),
                attempts: [
                    { reason: 'request-failed', message: 'ECONNREFUSED' },
                    { reason: 'invalid-location', message: 'redirect does not lead to a result page: https://www.google.com/sorry/index' },
                    { reason: 'no-redirect-status', message: 'response is not a redirect (status 200)' },
                ],
            }],
        });
        expect(summary.droppedRedirects[0].message).toContain('invalid-location');
    });

    it('describes a redirect with a valid status but no location', async () => {
        gotScrapingMock.mockResolvedValue({ statusCode: 302, headers: {} });

        const { summary } = await resolveGoogleRedirectUrlsWithSummary([{ title: 'title', url: 'https://www.google.com/goto?url=abc' }]);

        expect(summary.droppedRedirects[0]).toEqual(expect.objectContaining({
            originalUrl: 'https://www.google.com/goto?url=abc',
            // All three attempts failed the same way, so a single precise reason is reported
            reason: 'missing-location',
            message: 'redirect has no Location header (status 302)',
            attempts: [
                { reason: 'missing-location', message: 'redirect has no Location header (status 302)' },
                { reason: 'missing-location', message: 'redirect has no Location header (status 302)' },
                { reason: 'missing-location', message: 'redirect has no Location header (status 302)' },
            ],
        }));
    });

    it('does not ask Google for a page that has only local or plain links', async () => {
        const spy = vi.spyOn(Actor, 'createProxyConfiguration');

        const { summary } = await resolveGoogleRedirectUrlsWithSummary([
            { title: 'title', url: 'https://example.com/direct' },
            { title: 'title', url: 'https://www.google.com/url?q=https://example.com/local' },
        ]);

        expect(summary.localParamHits).toBe(1);
        expect(summary.untouched).toBe(1);
        expect(gotScrapingMock).not.toHaveBeenCalled();
        expect(spy).not.toHaveBeenCalled();
    });
});
