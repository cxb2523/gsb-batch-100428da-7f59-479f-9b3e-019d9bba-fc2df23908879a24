import {
    afterAll,
    beforeAll,
    beforeEach,
    describe,
    expect,
    it,
    vi,
} from 'vitest';
import { createServer, type Server } from 'node:http';
import { createServer as createNetServer } from 'node:net';
import { once } from 'node:events';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Socket } from 'node:net';

/**
 * These tests exercise the multi-page flow against a local, self-hosted set of
 * HTML pages. The URL policy is stubbed so the loopback test server is
 * reachable; the policy itself is covered by url-policy.test.ts.
 */
vi.mock('../src/utils/urlPolicy.js', async importOriginal => {
    const actual =
        await importOriginal<typeof import('../src/utils/urlPolicy.js')>();

    return {
        ...actual,
        resolvePublicHttpUrl: vi.fn(async (url: string) => {
            const parsed = new URL(url);
            return {
                url: parsed,
                hostname: parsed.hostname,
                addresses: [{ address: parsed.hostname, family: 4 as const }],
            };
        }),
        assertPublicHttpUrl: vi.fn(async (url: string) => new URL(url)),
    };
});

import { fetchMarkdown } from '../src/internal/fetchMarkdown.js';

const START_PAGE_HTML = `<!doctype html>
<html>
<head><title>Start Page</title></head>
<body>
<h1>Start Page</h1>
<p>This start page contains plenty of prose so that content extraction succeeds
and the crawler can follow the links that are listed below.</p>
<ul>
<li><a href="/blocked">Blocked page</a></li>
<li><a href="/slow">Slow page</a></li>
<li><a href="/b">Second page</a></li>
</ul>
</body>
</html>`;

const SECOND_PAGE_HTML = `<!doctype html>
<html>
<head><title>Second Page</title></head>
<body>
<h1>Second Page</h1>
<p>The second page also has enough prose to be extracted and returned as
markdown, proving that content after a failed page is still usable.</p>
</body>
</html>`;

const BLOCKED_PAGE_HTML = `<!doctype html>
<html>
<head><title>Blocked Page</title></head>
<body><h1>Blocked Page</h1><p>This page must never be fetched.</p></body>
</html>`;

// innerHTML is shorter than 50 characters so the extractor reports a page with
// only limited (JavaScript rendered) content. The bracketed links are resolved
// from the fallback markdown so the queue still advances.
const JS_ONLY_PAGE_HTML = `<!doctype html>
<html>
<head><title>JS Only</title></head>
<body><p>[a](/n) [b](/r)</p></body>
</html>`;

describe('fetchMarkdown multi-page outcomes', () => {
    let server: Server;
    let baseUrl = '';
    let deadUrl = '';
    let cacheDir = '';
    const hits = new Map<string, number>();
    const sockets = new Set<Socket>();

    beforeAll(async () => {
        server = createServer((req, res) => {
            const path = req.url ?? '/';
            hits.set(path, (hits.get(path) ?? 0) + 1);

            if (path === '/robots.txt') {
                // Served without a Content-Type, like a plain robots.txt body.
                // (@just-every/crawl's robots fetcher rejects explicit
                // non-HTML content types, but parses the text it receives.)
                res.writeHead(200);
                res.end('User-agent: *\nDisallow: /blocked\n');
                return;
            }

            if (path === '/slow') {
                // Never respond: forces the per-page request timeout.
                return;
            }

            if (path === '/n') {
                res.writeHead(200, { 'content-type': 'application/pdf' });
                res.end('%PDF-1.4 not html');
                return;
            }

            if (path === '/r') {
                // Drop the connection: an unreachable page mid-crawl.
                req.socket.destroy();
                return;
            }

            const htmlByPath: Record<string, string> = {
                '/': START_PAGE_HTML,
                '/b': SECOND_PAGE_HTML,
                '/blocked': BLOCKED_PAGE_HTML,
                '/js-only': JS_ONLY_PAGE_HTML,
            };
            const html = htmlByPath[path] ?? START_PAGE_HTML;
            res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
            res.end(html);
        });

        server.on('connection', socket => {
            sockets.add(socket);
            socket.on('close', () => sockets.delete(socket));
        });

        server.listen(0, '127.0.0.1');
        await once(server, 'listening');
        const address = server.address();
        if (!address || typeof address === 'string') {
            throw new Error('Failed to determine test server address');
        }
        baseUrl = `http://127.0.0.1:${address.port}`;

        // Reserve then release a port so a closed port is used for the
        // unreachable-address case.
        const probe = createNetServer();
        probe.listen(0, '127.0.0.1');
        await once(probe, 'listening');
        const probeAddress = probe.address();
        if (!probeAddress || typeof probeAddress === 'string') {
            throw new Error('Failed to determine probe port');
        }
        const deadPort = probeAddress.port;
        probe.close();
        await once(probe, 'close');
        deadUrl = `http://127.0.0.1:${deadPort}/unreachable`;

        cacheDir = await mkdtemp(join(tmpdir(), 'fetch-markdown-test-'));
    }, 30000);

    afterAll(async () => {
        for (const socket of sockets) {
            socket.destroy();
        }
        sockets.clear();
        server.close();
        await once(server, 'close');
        if (cacheDir) {
            await rm(cacheDir, { recursive: true, force: true });
        }
    });

    beforeEach(() => {
        hits.clear();
    });

    it('returns successful page content when some pages fail, keeping the queue moving', async () => {
        const result = await fetchMarkdown(`${baseUrl}/`, {
            maxPages: 4,
            timeout: 250,
            cacheDir,
        });

        // Every attempted page has exactly one explicit outcome, in crawl order.
        expect(result.pages?.map(page => page.category)).toEqual([
            'success',
            'blocked-by-robots',
            'fetch-error',
            'success',
        ]);
        expect(result.pages?.map(page => page.url)).toEqual([
            `${baseUrl}/`,
            `${baseUrl}/blocked`,
            `${baseUrl}/slow`,
            `${baseUrl}/b`,
        ]);

        // Failed pages are recorded with their category and reason.
        const blocked = result.pages?.[1];
        expect(blocked?.error).toContain('robots.txt');
        const timedOut = result.pages?.[2];
        expect(timedOut?.error?.toLowerCase()).toMatch(
            /abort|timeout|timed out/
        );

        // A failed page does not abort the rest of the queue: /b is fetched.
        expect(hits.get('/b')).toBe(1);
        expect(hits.get('/blocked')).toBeUndefined();

        // Successful content is still usable and keeps the original layout.
        expect(result.markdown).toContain(`<!-- Source: ${baseUrl}/ -->`);
        expect(result.markdown).toContain('This start page contains plenty');
        expect(result.markdown).toContain(`<!-- Source: ${baseUrl}/b -->`);
        expect(result.markdown).toContain('The second page also has enough');
        expect(result.markdown).toMatch(/\n\n---\n\n/);

        // Failed pages say which page is missing and why, in their own slot.
        expect(result.markdown).toContain(
            `<!-- Source: ${baseUrl}/blocked -->`
        );
        expect(result.markdown).toContain('Failed page: blocked by robots.txt');
        expect(result.markdown).toContain(`<!-- Source: ${baseUrl}/slow -->`);
        expect(result.markdown).toContain('Failed page: fetch failed');

        // The top-level error summarizes counts and categories, not URLs.
        expect(result.error).toBe(
            '2 of 4 pages failed: 1 blocked by robots.txt, 1 fetch error'
        );
        expect(result.error).not.toContain(baseUrl);

        // The first successful page still supplies the document title.
        expect(result.title).toBe('Start Page');
    }, 20000);

    it('reports a checkable outcome for every page when all pages fail', async () => {
        const result = await fetchMarkdown(`${baseUrl}/js-only`, {
            maxPages: 3,
            timeout: 250,
            cacheDir,
        });

        expect(result.pages?.map(page => page.category)).toEqual([
            'content-unavailable',
            'fetch-error',
            'fetch-error',
        ]);
        expect(result.pages?.map(page => page.url)).toEqual([
            `${baseUrl}/js-only`,
            `${baseUrl}/n`,
            `${baseUrl}/r`,
        ]);

        // Each failed page keeps its slot and names its failure category.
        expect(result.markdown).toContain(
            `<!-- Source: ${baseUrl}/js-only -->`
        );
        expect(result.markdown).toContain(
            'Failed page: content unavailable (content-unavailable)'
        );
        expect(result.markdown).toContain('Failed page: fetch failed');
        expect(result.markdown).toContain(`<!-- Source: ${baseUrl}/n -->`);
        expect(result.markdown).toContain(`<!-- Source: ${baseUrl}/r -->`);

        // Partially extracted content is still surfaced, not discarded.
        expect(result.markdown).toContain('JavaScript-rendered');

        expect(result.error).toBe(
            '3 of 3 pages failed: 2 fetch errors, 1 content unavailable'
        );
        expect(result.error).not.toContain(baseUrl);
    }, 20000);

    it('keeps single-page success output compatible with existing callers', async () => {
        const result = await fetchMarkdown(`${baseUrl}/`, {
            maxPages: 1,
            cacheDir,
        });

        expect(
            result.markdown.startsWith(`<!-- Source: ${baseUrl}/ -->\n`)
        ).toBe(true);
        expect(result.markdown).toContain('This start page contains plenty');
        expect(result.title).toBe('Start Page');
        expect(result.error).toBeUndefined();
        expect(Array.isArray(result.links)).toBe(true);
        expect(result.pages?.map(page => page.category)).toEqual(['success']);
    }, 20000);

    it('keeps the single-page failure shape and reports an unreachable address', async () => {
        const page = `${baseUrl}/n`;
        const nonHtml = await fetchMarkdown(page, { maxPages: 1, cacheDir });

        expect(
            nonHtml.markdown.startsWith(`<!-- Error fetching ${page}: `)
        ).toBe(true);
        expect(nonHtml.markdown).toContain('Non-HTML content type');
        expect(nonHtml.pages?.map(entry => entry.category)).toEqual([
            'fetch-error',
        ]);
        // Single-page callers see the historical error string.
        expect(nonHtml.error).toBe(`Some pages had errors: ${page}`);

        const unreachable = await fetchMarkdown(deadUrl, {
            maxPages: 1,
            timeout: 2000,
            cacheDir,
        });

        expect(
            unreachable.markdown.startsWith(`<!-- Error fetching ${deadUrl}: `)
        ).toBe(true);
        expect(unreachable.pages).toHaveLength(1);
        expect(unreachable.pages?.[0]).toMatchObject({
            url: deadUrl,
            category: 'fetch-error',
        });
        expect(unreachable.error).toBe(`Some pages had errors: ${deadUrl}`);
    }, 20000);

    it('counts cached hits and cache misses the same way', async () => {
        const options = { maxPages: 4, timeout: 250, cacheDir };

        const first = await fetchMarkdown(`${baseUrl}/`, options);
        hits.clear();
        const second = await fetchMarkdown(`${baseUrl}/`, options);

        const outcomeKey = (entries: typeof first.pages) =>
            (entries ?? []).map(entry => [entry.url, entry.category]);

        expect(outcomeKey(second.pages)).toEqual(outcomeKey(first.pages));

        // Successful pages came from the cache on the second pass.
        expect(hits.get('/')).toBeUndefined();
        expect(hits.get('/b')).toBeUndefined();
        // Pages that failed are not cached, so they are retried.
        expect(hits.get('/slow')).toBe(1);
        // The blocked page is never requested at all.
        expect(hits.get('/blocked')).toBeUndefined();
    }, 20000);
});
