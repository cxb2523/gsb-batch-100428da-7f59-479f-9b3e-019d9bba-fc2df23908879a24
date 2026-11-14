import http from 'http';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { StoredProfile, TimezoneResolution } from '../types';
import {
    detectExitLocation,
    formatTimezoneResolution,
    logTimezoneResolution,
    probeExitLocation,
    resolveTimezone,
} from './proxy';
import type { GeoLookupResult } from './proxy';

const servers: http.Server[] = [];

afterEach(async () => {
    await Promise.all(servers.splice(0).map((server) => new Promise<void>((resolve) => {
        server.closeAllConnections?.();
        server.close(() => resolve());
    })));
});

interface FakeRelay {
    url: string;
    requested: () => string | undefined;
}

/**
 * A forward proxy stand-in: records what it was asked for and answers like ip-api.com.
 * `statusCode` fakes an ip-api.com rejection; `hold` never answers at all.
 */
async function fakeRelay(
    body: string,
    options: { statusCode?: number; hold?: boolean } = {}
): Promise<FakeRelay> {
    let requested: string | undefined;
    const server = http.createServer((request, response) => {
        requested = request.url;
        if (options.hold) return; // Keep the socket open: the client has to time out.
        if (options.statusCode && options.statusCode >= 400) {
            response.writeHead(options.statusCode);
            response.end(body);
            return;
        }
        response.end(body);
    });
    servers.push(server);
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    const { port } = server.address() as { port: number };
    return { url: `http://127.0.0.1:${port}`, requested: () => requested };
}

/** A URL whose port nothing listens on: the relay itself is unreachable. */
async function deadRelayUrl(): Promise<string> {
    const server = http.createServer();
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    const { port } = server.address() as { port: number };
    await new Promise<void>((resolve) => server.close(() => resolve()));
    return `http://127.0.0.1:${port}`;
}

const SUCCESS_BODY = JSON.stringify({
    status: 'success', country: 'United States', regionName: 'New Jersey', city: 'Verona',
    timezone: 'America/New_York',
});

function geoSuccess(geo: { timezone: string; country?: string; city?: string; region?: string }): GeoLookupResult {
    return {
        ok: true,
        geo: { country: '', city: '', region: '', ...geo },
    };
}

function proxyProfile(overrides: Partial<StoredProfile> = {}): StoredProfile {
    return {
        id: 'proxied', name: 'Proxied', createdAt: 0, updatedAt: 0,
        proxy: { type: 'http', host: 'gate.provider.net', port: 8080 },
        ...overrides,
    };
}

describe('probeExitLocation', () => {
    it('asks through the relay for the caller IP and reports success', async () => {
        const relay = await fakeRelay(SUCCESS_BODY);

        const result = await probeExitLocation(relay.url);

        expect(relay.requested()).toMatch(/^http:\/\/ip-api\.com\/json\/\?fields=/);
        expect(result).toEqual({
            ok: true,
            geo: { timezone: 'America/New_York', country: 'United States', city: 'Verona', region: 'New Jersey' },
        });
    });

    it('keeps the reason when ip-api.com answers status=fail', async () => {
        const relay = await fakeRelay(JSON.stringify({ status: 'fail' }));

        const result = await probeExitLocation(relay.url);

        expect(result.ok).toBe(false);
        if (result.ok) throw new Error('expected a failure');
        expect(result.failure).toEqual({
            step: 'relay-exit-ip',
            code: 'not-success',
            detail: 'ip-api.com answered status=fail',
        });
    });

    it('keeps the reason when the answer is not JSON', async () => {
        const relay = await fakeRelay('<html>blocked</html>');

        const result = await probeExitLocation(relay.url);

        expect(result.ok).toBe(false);
        if (result.ok) throw new Error('expected a failure');
        expect(result.failure.code).toBe('invalid-json');
        expect(result.failure.detail).toContain('<html>blocked</html>');
    });

    it('keeps the reason when the relay is unreachable', async () => {
        const result = await probeExitLocation(await deadRelayUrl(), 2000);

        expect(result.ok).toBe(false);
        if (result.ok) throw new Error('expected a failure');
        expect(result.failure.step).toBe('relay-exit-ip');
        expect(result.failure.code).toBe('unreachable');
        expect(result.failure.detail).toMatch(/relay did not answer/);
    });

    it('keeps the reason when the relay never answers', async () => {
        const relay = await fakeRelay('', { hold: true });

        const result = await probeExitLocation(relay.url, 100);

        expect(result.ok).toBe(false);
        if (result.ok) throw new Error('expected a failure');
        expect(result.failure.code).toBe('timeout');
        expect(result.failure.detail).toContain('100 ms');
    });
});

describe('detectExitLocation', () => {
    it('returns the location or null, keeping the old signature', async () => {
        const relay = await fakeRelay(SUCCESS_BODY);
        expect(await detectExitLocation(relay.url)).toEqual({
            timezone: 'America/New_York', country: 'United States', city: 'Verona', region: 'New Jersey',
        });

        const failed = await fakeRelay(JSON.stringify({ status: 'fail' }));
        expect(await detectExitLocation(failed.url)).toBeNull();

        const garbage = await fakeRelay('<html>blocked</html>');
        expect(await detectExitLocation(garbage.url)).toBeNull();

        expect(await detectExitLocation(await deadRelayUrl(), 2000)).toBeNull();
    });
});

describe('resolveTimezone sources', () => {
    it('reports a profile timezone without touching the relay', async () => {
        const relay = await fakeRelay(SUCCESS_BODY);
        const lookupHost = vi.fn();

        const resolution = await resolveTimezone(
            proxyProfile({ timezone: 'Asia/Tokyo' }),
            relay.url,
            { lookupHost }
        );

        expect(resolution).toEqual({ timezone: 'Asia/Tokyo', source: 'profile', failures: [] });
        expect(relay.requested()).toBeUndefined();
        expect(lookupHost).not.toHaveBeenCalled();
    });

    it('reports the relay exit location when the probe succeeds', async () => {
        const relay = await fakeRelay(SUCCESS_BODY);
        const lookupHost = vi.fn();

        const resolution = await resolveTimezone(proxyProfile(), relay.url, { lookupHost });

        expect(resolution).toEqual({
            timezone: 'America/New_York',
            source: 'relay-exit-ip',
            detail: 'Verona, United States',
            failures: [],
        });
        // The host lookup is only a fallback; a working relay must not trigger it.
        expect(lookupHost).not.toHaveBeenCalled();
    });

    it('falls back to the proxy host and keeps the status=fail reason', async () => {
        const relay = await fakeRelay(JSON.stringify({ status: 'fail' }));
        const lookupHost = vi.fn(async () => geoSuccess({ timezone: 'Europe/Paris', city: 'Paris', country: 'France' }));

        const resolution = await resolveTimezone(proxyProfile(), relay.url, { lookupHost });

        expect(resolution.source).toBe('proxy-host');
        expect(resolution.timezone).toBe('Europe/Paris');
        expect(resolution.detail).toBe('Paris, France');
        expect(resolution.failures).toEqual([{
            step: 'relay-exit-ip',
            code: 'not-success',
            detail: 'ip-api.com answered status=fail',
        }]);
        expect(lookupHost).toHaveBeenCalledWith('gate.provider.net');
    });

    it('falls back to the proxy host and keeps the invalid-json reason', async () => {
        const relay = await fakeRelay('<html>blocked</html>');
        const lookupHost = vi.fn(async () => geoSuccess({ timezone: 'Europe/Paris', city: 'Paris', country: 'France' }));

        const resolution = await resolveTimezone(proxyProfile(), relay.url, { lookupHost });

        expect(resolution.source).toBe('proxy-host');
        expect(resolution.failures).toHaveLength(1);
        expect(resolution.failures[0].code).toBe('invalid-json');
        expect(resolution.failures[0].detail).toContain('<html>blocked</html>');
    });

    it('falls back to the proxy host and keeps the unreachable reason', async () => {
        const relayUrl = await deadRelayUrl();
        const lookupHost = vi.fn(async () => geoSuccess({ timezone: 'Europe/Paris', city: 'Paris', country: 'France' }));

        const resolution = await resolveTimezone(proxyProfile(), relayUrl, { lookupHost });

        expect(resolution.source).toBe('proxy-host');
        expect(resolution.failures).toEqual([expect.objectContaining({
            step: 'relay-exit-ip', code: 'unreachable',
        })]);
    });

    it('keeps both reasons when every lookup fails, and never throws', async () => {
        const relay = await fakeRelay(JSON.stringify({ status: 'fail' }));
        const lookupHost = vi.fn(async (): Promise<GeoLookupResult> => ({
            ok: false,
            failure: { step: 'proxy-host', code: 'timeout', detail: 'ip-api.com did not answer within 10000 ms' },
        }));

        const resolution = await resolveTimezone(proxyProfile(), relay.url, {
            lookupHost,
            systemTimezone: () => 'Asia/Tokyo',
        });

        expect(resolution.source).toBe('system');
        expect(resolution.timezone).toBe('Asia/Tokyo');
        expect(resolution.failures.map((failure) => [failure.step, failure.code])).toEqual([
            ['relay-exit-ip', 'not-success'],
            ['proxy-host', 'timeout'],
        ]);
    });

    it('uses the system timezone when the profile has no proxy', async () => {
        const relay = await fakeRelay(SUCCESS_BODY);

        const resolution = await resolveTimezone(
            proxyProfile({ proxy: undefined }),
            relay.url,
            { systemTimezone: () => 'Asia/Tokyo' }
        );

        expect(resolution).toEqual({ timezone: 'Asia/Tokyo', source: 'system', failures: [] });
        expect(relay.requested()).toBeUndefined();
    });

    it('turns a throwing probe into a failure reason instead of failing the launch', async () => {
        const resolution = await resolveTimezone(proxyProfile(), 'http://127.0.0.1:1', {
            probeExit: async () => { throw new Error('boom'); },
            lookupHost: async () => ({ ok: true, geo: { timezone: 'Europe/Paris', country: '', city: '', region: '' } }),
        });

        expect(resolution.source).toBe('proxy-host');
        expect(resolution.failures).toEqual([expect.objectContaining({
            step: 'relay-exit-ip', code: 'unreachable', detail: 'the lookup threw: boom',
        })]);
    });

    it('never throws even when both lookups throw', async () => {
        const resolution = await resolveTimezone(proxyProfile(), 'http://127.0.0.1:1', {
            probeExit: async () => { throw new Error('relay down'); },
            lookupHost: async () => { throw new Error('api down'); },
            systemTimezone: () => 'Asia/Tokyo',
        });

        expect(resolution.source).toBe('system');
        expect(resolution.failures.map((failure) => failure.step)).toEqual(['relay-exit-ip', 'proxy-host']);
    });

    it('records why no relay was available instead of hiding it', async () => {
        const lookupHost = vi.fn(async () => geoSuccess({
            timezone: 'America/New_York', city: 'Verona', country: 'United States',
        }));

        const resolution = await resolveTimezone(proxyProfile(), undefined, { lookupHost });

        expect(resolution.source).toBe('proxy-host');
        expect(resolution.failures).toEqual([expect.objectContaining({
            step: 'relay-exit-ip', code: 'no-relay',
        })]);
    });
});

describe('timezone logging', () => {
    const resolved: TimezoneResolution = {
        timezone: 'America/New_York',
        source: 'relay-exit-ip',
        detail: 'Verona, United States',
        failures: [],
    };

    it('names the source and location in one line', () => {
        expect(formatTimezoneResolution(resolved)).toBe(
            'Timezone for Chrome: America/New_York (source: relay-exit-ip, Verona, United States)'
        );
    });

    it('names every failure that led to a fallback', () => {
        const line = formatTimezoneResolution({
            timezone: 'Asia/Tokyo',
            source: 'system',
            failures: [
                { step: 'relay-exit-ip', code: 'unreachable', detail: 'relay did not answer: ECONNREFUSED' },
                { step: 'proxy-host', code: 'timeout', detail: 'ip-api.com did not answer within 10000 ms' },
            ],
        });
        expect(line).toContain('source: system');
        expect(line).toContain('relay-exit-ip failed: unreachable');
        expect(line).toContain('proxy-host failed: timeout');
    });

    it('warns with the source and every reason, so no fallback is silent', () => {
        const warn = vi.fn();
        const info = vi.fn();

        logTimezoneResolution({
            timezone: 'Asia/Tokyo',
            source: 'system',
            failures: [{ step: 'relay-exit-ip', code: 'not-success', detail: 'ip-api.com answered status=fail' }],
        }, { debug: vi.fn(), info, warn, error: vi.fn() });

        expect(info).not.toHaveBeenCalled();
        expect(warn).toHaveBeenCalledTimes(2);
        expect(warn.mock.calls[0][0]).toContain('source: system');
        expect(warn.mock.calls[0][0]).toContain('not-success');
        expect(warn.mock.calls[1][0]).toContain('relay-exit-ip failed: not-success');
    });

    it('logs a clean resolution at info level', () => {
        const warn = vi.fn();
        const info = vi.fn();

        logTimezoneResolution(resolved, { debug: vi.fn(), info, warn, error: vi.fn() });

        expect(warn).not.toHaveBeenCalled();
        expect(info).toHaveBeenCalledWith(
            'Timezone for Chrome: America/New_York (source: relay-exit-ip, Verona, United States)'
        );
    });
});
