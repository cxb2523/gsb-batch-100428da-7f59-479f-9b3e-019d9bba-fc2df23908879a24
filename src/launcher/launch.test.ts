import fs from 'fs';
import http from 'http';
import os from 'os';
import path from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LaunchResult, StoredProfile } from '../types';
import { readLockFile } from '../storage';
import type { CdpParams } from './deps';
import { launchChrome } from './launch';

// Chrome and its DevTools socket are faked; everything between them is the real launcher.
const fake = vi.hoisted(() => ({
    port: 0,
    relayUrl: '',
    relays: [] as Array<{ close: () => Promise<void> }>,
    launches: [] as Array<{ chromeFlags: string[]; envVars?: Record<string, string>; port?: number }>,
    cdpCalls: [] as string[],
}));

vi.mock('./deps', () => ({
    loadChromeLauncher: async () => ({
        launch: async (options: { chromeFlags: string[]; envVars?: Record<string, string>; port?: number }) => {
            fake.launches.push(options);
            return { port: fake.port, pid: 2_000_000_000, kill: async () => undefined };
        },
    }),
    loadCdp: async () => async () => ({
        send: async (method: string): Promise<CdpParams> => {
            fake.cdpCalls.push(method);
            if (method === 'Browser.getVersion') return { product: 'Chrome/150.0.7000.1' };
            if (method === 'Target.getTargets') return { targetInfos: [] };
            return {};
        },
        on: () => undefined,
        close: async () => undefined,
    }),
    loadProxyChain: async () => ({
        anonymizeProxy: async () => fake.relayUrl,
        closeAnonymizedProxy: async () => undefined,
    }),
}));

let server: http.Server;
const servers: http.Server[] = [];
let dir: string;
let chromePath: string;
let launched: LaunchResult | null = null;

beforeEach(async () => {
    fake.launches.length = 0;
    fake.cdpCalls.length = 0;
    fake.relayUrl = '';
    dir = fs.mkdtempSync(path.join(os.tmpdir(), 'launch-'));
    chromePath = path.join(dir, 'chrome');
    fs.writeFileSync(chromePath, 'stock chrome');
    server = http.createServer((_request, response) => {
        response.end(JSON.stringify({ webSocketDebuggerUrl: `ws://127.0.0.1:${fake.port}/devtools/browser/fake` }));
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    fake.port = (server.address() as { port: number }).port;
});

afterEach(async () => {
    if (launched) await launched.close();
    launched = null;
    await new Promise<void>((resolve) => server.close(() => resolve()));
    for (const relay of servers.splice(0)) {
        relay.closeAllConnections?.();
        relay.close();
    }
    fs.rmSync(dir, { recursive: true, force: true });
});

const realProfile: StoredProfile = {
    id: 'real-1', name: 'Google Main', createdAt: 0, updatedAt: 0, fingerprint: { mode: 'real' },
};

function launch(profile: StoredProfile, engine?: 'auto' | 'kernel' | 'inject'): Promise<LaunchResult> {
    return launchChrome({ profile, userDataDir: path.join(dir, 'data'), chromePath, engine });
}

const SPOOF_FLAG = /^--(fingerprint|lang=|accept-lang|timezone|disable-features=IsolateOrigins|webrtc-ip)/;

describe('launchChrome with a real profile', () => {
    it('passes no fingerprint flag, no TZ and sends no override to any tab', async () => {
        launched = await launch(realProfile);

        expect(launched.engine).toBe('real');
        const [{ chromeFlags, envVars }] = fake.launches;
        expect(chromeFlags.filter((flag) => SPOOF_FLAG.test(flag))).toEqual([]);
        expect(chromeFlags).not.toContain('--no-sandbox');
        expect(chromeFlags).not.toContain('--disable-background-networking');
        expect(chromeFlags).not.toContain('--disable-client-side-phishing-detection');
        expect(chromeFlags).toContain(`--user-data-dir=${path.join(dir, 'data')}`);
        expect(envVars).toBeUndefined();
        expect(fake.cdpCalls).toEqual([]);
        expect(readLockFile(path.join(dir, 'data'))?.engine).toBe('real');
    });

    it('applies only the timezone and language the profile pins itself', async () => {
        launched = await launch({
            ...realProfile, timezone: 'Asia/Ho_Chi_Minh', fingerprint: { mode: 'real', language: 'vi-VN' },
        });

        const [{ chromeFlags, envVars }] = fake.launches;
        expect(envVars).toEqual({ TZ: 'Asia/Ho_Chi_Minh' });
        expect(chromeFlags).toContain('--lang=vi-VN');
        expect(fake.cdpCalls).toEqual([]);
    });

    it('refuses the kernel engine before Chrome starts and releases the profile', async () => {
        await expect(launch(realProfile, 'kernel')).rejects.toThrow(/real.*engine "kernel".*--fingerprint generated/s);
        await expect(launch(realProfile, 'inject')).rejects.toThrow(/engine "inject"/);
        expect(fake.launches).toEqual([]);
        expect(readLockFile(path.join(dir, 'data'))).toBeNull();
    });
});

describe('launchChrome with a generated profile', () => {
    it('still spoofs: TZ, --lang, anti-detect flags and auto-attach', async () => {
        launched = await launch({ id: 'gen-1', name: 'Gen', createdAt: 0, updatedAt: 0, timezone: 'Europe/Paris' });

        expect(launched.engine).toBe('inject');
        const [{ chromeFlags, envVars }] = fake.launches;
        expect(envVars).toEqual({ TZ: 'Europe/Paris' });
        expect(chromeFlags).toEqual(expect.arrayContaining([
            '--no-sandbox',
            '--disable-background-networking',
            '--lang=en-US',
            '--disable-features=IsolateOrigins,site-per-process',
            '--webrtc-ip-handling-policy=disable_non_proxied_udp',
        ]));
        expect(fake.cdpCalls).toEqual(expect.arrayContaining(['Browser.getVersion', 'Target.setAutoAttach']));
    });
});

describe('launchChrome DevTools port', () => {
    it('asks Chrome for the fixed port, and for none by default', async () => {
        launched = await launchChrome({ profile: realProfile, userDataDir: path.join(dir, 'data'), chromePath, port: 9301 });
        await launched.close();
        launched = await launch(realProfile);

        expect(fake.launches.map((options) => options.port)).toEqual([9301, undefined]);
    });
});

/** A local relay stand-in answering however ip-api.com would have. */
async function startFakeRelay(answer: string): Promise<void> {
    const relay = http.createServer((_request, response) => response.end(answer));
    servers.push(relay);
    await new Promise<void>((resolve) => relay.listen(0, '127.0.0.1', resolve));
    fake.relayUrl = `http://127.0.0.1:${(relay.address() as { port: number }).port}`;
}

/** A relay URL nothing answers on, so the probe fails as unreachable. */
async function deadRelayUrl(): Promise<void> {
    const probe = http.createServer();
    await new Promise<void>((resolve) => probe.listen(0, '127.0.0.1', resolve));
    fake.relayUrl = `http://127.0.0.1:${(probe.address() as { port: number }).port}`;
    await new Promise<void>((resolve) => probe.close(() => resolve()));
}

const GEO_SUCCESS = JSON.stringify({
    status: 'success', country: 'United States', regionName: 'New Jersey', city: 'Verona',
    timezone: 'America/New_York',
});

function proxyProfile(overrides: Partial<StoredProfile> = {}): StoredProfile {
    return {
        id: 'gen-proxy', name: 'Proxied', createdAt: 0, updatedAt: 0,
        proxy: { type: 'http', host: 'gate.provider.net', port: 8080 },
        ...overrides,
    };
}

/** ip-api.com is never really contacted: the direct host lookup gets this answer. */
function stubHostLookup(answer: string, ok = true): void {
    const realFetch = globalThis.fetch;
    vi.stubGlobal('fetch', async (input: string | URL | Request, init?: RequestInit) => {
        const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        // Chrome's DevTools probe also uses fetch; only the GeoIP request is answered here.
        if (!url.includes('ip-api.com')) return realFetch(input, init);
        return {
            ok,
            status: ok ? 200 : 429,
            text: async () => answer,
        };
    });
}

function captureStderr(): { text: () => string; restore: () => void } {
    const chunks: string[] = [];
    const spy = vi.spyOn(process.stderr, 'write').mockImplementation((chunk: unknown) => {
        chunks.push(String(chunk));
        return true;
    });
    return { text: () => chunks.join(''), restore: () => spy.mockRestore() };
}

describe('launchChrome timezone origin in the startup log', () => {
    const originalDebug = process.env.DEBUG;

    beforeEach(() => {
        delete process.env.DEBUG;
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        if (originalDebug === undefined) delete process.env.DEBUG;
        else process.env.DEBUG = originalDebug;
        for (const relay of servers.splice(0)) relay.closeAllConnections?.(), relay.close();
    });

    it('reports a relay exit location, and uses it for TZ', async () => {
        await startFakeRelay(GEO_SUCCESS);
        process.env.DEBUG = 'browser-profiles';
        const stderr = captureStderr();
        try {
            launched = await launch(proxyProfile());
        } finally {
            stderr.restore();
        }

        expect(launched.timezone).toEqual({
            timezone: 'America/New_York',
            source: 'relay-exit-ip',
            detail: 'Verona, United States',
            failures: [],
        });
        expect(fake.launches[0].envVars).toEqual({ TZ: 'America/New_York' });
        expect(stderr.text()).toContain(
            'Timezone for Chrome: America/New_York (source: relay-exit-ip, Verona, United States)'
        );
    });

    it('warns with the reason when ip-api.com answers status=fail and the host lookup also fails', async () => {
        await startFakeRelay(JSON.stringify({ status: 'fail' }));
        stubHostLookup(JSON.stringify({ status: 'fail' }));
        const stderr = captureStderr();
        try {
            launched = await launch(proxyProfile());
        } finally {
            stderr.restore();
        }

        expect(launched.timezone?.source).toBe('system');
        expect(launched.timezone?.failures.map((failure) => [failure.step, failure.code])).toEqual([
            ['relay-exit-ip', 'not-success'],
            ['proxy-host', 'not-success'],
        ]);
        // Failures always reach stderr, no --verbose needed.
        expect(stderr.text()).toContain('source: system');
        expect(stderr.text()).toContain('relay-exit-ip failed: not-success (ip-api.com answered status=fail)');
        expect(stderr.text()).toContain('proxy-host failed: not-success (ip-api.com answered status=fail)');
    });

    it('warns with the reason when the relay answers garbage but the proxy host resolves', async () => {
        await startFakeRelay('<html>blocked</html>');
        stubHostLookup(GEO_SUCCESS);
        const stderr = captureStderr();
        try {
            launched = await launch(proxyProfile());
        } finally {
            stderr.restore();
        }

        expect(launched.timezone?.source).toBe('proxy-host');
        expect(launched.timezone?.timezone).toBe('America/New_York');
        expect(launched.timezone?.failures[0].code).toBe('invalid-json');
        expect(fake.launches[0].envVars).toEqual({ TZ: 'America/New_York' });
        expect(stderr.text()).toContain('source: proxy-host, Verona, United States');
        expect(stderr.text()).toContain('relay-exit-ip failed: invalid-json');
    });

    it('warns with the reason when the relay is unreachable', async () => {
        await deadRelayUrl();
        stubHostLookup(GEO_SUCCESS);
        const stderr = captureStderr();
        try {
            launched = await launch(proxyProfile());
        } finally {
            stderr.restore();
        }

        expect(launched.timezone?.source).toBe('proxy-host');
        expect(launched.timezone?.failures).toEqual([expect.objectContaining({
            step: 'relay-exit-ip', code: 'unreachable',
        })]);
        expect(stderr.text()).toContain('relay-exit-ip failed: unreachable');
    });

    it('never fails the launch when every lookup fails', async () => {
        await deadRelayUrl();
        stubHostLookup('', false);
        const stderr = captureStderr();
        try {
            launched = await launch(proxyProfile());
        } finally {
            stderr.restore();
        }

        expect(launched.engine).toBe('inject');
        expect(launched.timezone?.source).toBe('system');
        expect(launched.timezone?.timezone).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
        expect(launched.timezone?.failures.map((failure) => failure.code)).toEqual([
            'unreachable', 'http-error',
        ]);
        expect(stderr.text()).toContain('source: system');
    });
});
