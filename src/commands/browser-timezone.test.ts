import fs from 'fs';
import http from 'http';
import os from 'os';
import path from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CdpParams } from '../launcher/deps';
import type { LaunchResult, StoredProfile } from '../types';
import type { CommandContext } from './define';
import { createCommandContext, runCommand } from './define';
import { getCommand } from './registry';

// Chrome and its DevTools socket are faked; the timezone lookup runs for real through
// a local relay, so the command's startup log is the one an operator would see.
const fake = vi.hoisted(() => ({
    port: 0,
    relayUrl: '',
    launches: [] as Array<{ chromeFlags: string[]; envVars?: Record<string, string> }>,
}));

vi.mock('../launcher/deps', () => ({
    loadChromeLauncher: async () => ({
        launch: async (options: { chromeFlags: string[]; envVars?: Record<string, string> }) => {
            fake.launches.push(options);
            return { port: fake.port, pid: 2_000_000_000, kill: async () => undefined };
        },
    }),
    loadCdp: async () => async () => ({
        send: async (method: string): Promise<CdpParams> => {
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

const servers: http.Server[] = [];
let storagePath: string;
let devTools: http.Server;
let ctx: CommandContext;
let opened: LaunchResult | null = null;
const originalDebug = process.env.DEBUG;

/** ip-api.com is never contacted: only its URLs get this answer. */
function stubHostLookup(answer: string): void {
    const realFetch = globalThis.fetch;
    vi.stubGlobal('fetch', async (input: string | URL | Request, init?: RequestInit) => {
        const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        if (!url.includes('ip-api.com')) return realFetch(input, init);
        return { ok: true, status: 200, text: async () => answer };
    });
}

/** A local relay that answers however ip-api.com would have. */
async function startRelay(answer: string): Promise<void> {
    const relay = http.createServer((_request, response) => response.end(answer));
    servers.push(relay);
    await new Promise<void>((resolve) => relay.listen(0, '127.0.0.1', resolve));
    fake.relayUrl = `http://127.0.0.1:${(relay.address() as { port: number }).port}`;
}

/** A relay URL nothing answers on. */
async function deadRelay(): Promise<void> {
    const probe = http.createServer();
    await new Promise<void>((resolve) => probe.listen(0, '127.0.0.1', resolve));
    fake.relayUrl = `http://127.0.0.1:${(probe.address() as { port: number }).port}`;
    await new Promise<void>((resolve) => probe.close(() => resolve()));
}

beforeEach(async () => {
    fake.launches.length = 0;
    fake.relayUrl = '';
    // A clean resolution is an info line: turn it on so the startup log is observable.
    process.env.DEBUG = 'browser-profiles';
    storagePath = fs.mkdtempSync(path.join(os.tmpdir(), 'bp-tz-'));
    ctx = createCommandContext({ storagePath });
    devTools = http.createServer((_request, response) => {
        response.end(JSON.stringify({ webSocketDebuggerUrl: `ws://127.0.0.1:${fake.port}/devtools/browser/fake` }));
    });
    await new Promise<void>((resolve) => devTools.listen(0, '127.0.0.1', resolve));
    fake.port = (devTools.address() as { port: number }).port;
});

afterEach(async () => {
    vi.unstubAllGlobals();
    if (originalDebug === undefined) delete process.env.DEBUG;
    else process.env.DEBUG = originalDebug;
    if (opened) await opened.close();
    opened = null;
    await new Promise<void>((resolve) => devTools.close(() => resolve()));
    for (const server of servers.splice(0)) {
        server.closeAllConnections?.();
        server.close();
    }
    fs.rmSync(storagePath, { recursive: true, force: true });
});

function command(name: string) {
    const def = getCommand(name);
    if (!def) throw new Error(`missing command ${name}`);
    return def;
}

function captureStderr(): { text: () => string; restore: () => void } {
    const chunks: string[] = [];
    const spy = vi.spyOn(process.stderr, 'write').mockImplementation((chunk: unknown) => {
        chunks.push(String(chunk));
        return true;
    });
    return { text: () => chunks.join(''), restore: () => spy.mockRestore() };
}

const GEO_SUCCESS = JSON.stringify({
    status: 'success', country: 'United States', regionName: 'New Jersey', city: 'Verona',
    timezone: 'America/New_York',
});

const PROXY_URL = 'http://gate.provider.net:8080';

interface OpenResult {
    timezone: LaunchResult['timezone'];
    rendered: string;
}

/** Open the profile and hand back both the command output and the live launch handle. */
async function openProfile(): Promise<OpenResult> {
    const created = await runCommand(ctx, command('profile.create'), { name: 'Proxied', proxy: PROXY_URL });
    if (!created.ok) throw new Error(created.error.message);
    const profile = created.data as StoredProfile;
    // create() stores the New_York default; this profile pins nothing, so detection runs.
    await ctx.profiles.update(profile.id, { timezone: undefined });

    const result = await runCommand(ctx, command('browser.open'), { idOrName: profile.id });
    if (!result.ok) throw new Error(result.error.message);
    opened = ctx.profiles.getRunning().get(profile.id) ?? null;

    return {
        timezone: (result.data as { timezone?: LaunchResult['timezone'] }).timezone,
        rendered: command('browser.open').render(result.data),
    };
}

describe('browser.open startup log names the timezone source', () => {
    it('reports the relay exit location and shows it in the rendered output', async () => {
        await startRelay(GEO_SUCCESS);
        const stderr = captureStderr();
        let openedResult: OpenResult;
        try {
            openedResult = await openProfile();
        } finally {
            stderr.restore();
        }

        expect(openedResult.timezone).toEqual({
            timezone: 'America/New_York',
            source: 'relay-exit-ip',
            detail: 'Verona, United States',
            failures: [],
        });
        expect(stderr.text()).toContain(
            'Timezone for Chrome: America/New_York (source: relay-exit-ip, Verona, United States)'
        );
        expect(openedResult.rendered).toContain(
            'timezone:   America/New_York (source: relay-exit-ip, Verona, United States)'
        );
    });

    it('keeps the failure reason when the relay answers status=fail, and warns about it', async () => {
        await startRelay(JSON.stringify({ status: 'fail' }));
        stubHostLookup(GEO_SUCCESS);
        const stderr = captureStderr();
        let timezone: LaunchResult['timezone'];
        try {
            ({ timezone } = await openProfile());
        } finally {
            stderr.restore();
        }

        expect(timezone?.source).toBe('proxy-host');
        expect(timezone?.failures).toEqual([{
            step: 'relay-exit-ip', code: 'not-success', detail: 'ip-api.com answered status=fail',
        }]);
        expect(stderr.text()).toContain('source: proxy-host, Verona, United States');
        expect(stderr.text()).toContain('relay-exit-ip failed: not-success (ip-api.com answered status=fail)');
    });

    it('names the relay probe failure that led to the host fallback', async () => {
        await startRelay('<html>blocked</html>');
        stubHostLookup(GEO_SUCCESS);
        const stderr = captureStderr();
        let timezone: LaunchResult['timezone'];
        try {
            ({ timezone } = await openProfile());
        } finally {
            stderr.restore();
        }

        expect(timezone?.failures[0].code).toBe('invalid-json');
        expect(stderr.text()).toContain('relay-exit-ip failed: invalid-json');
    });

    it('does not fail the launch when every lookup fails, and says where it fell back', async () => {
        await deadRelay();
        stubHostLookup(JSON.stringify({ status: 'fail' }));
        const stderr = captureStderr();
        let timezone: LaunchResult['timezone'];
        try {
            ({ timezone } = await openProfile());
        } finally {
            stderr.restore();
        }

        expect(timezone?.source).toBe('system');
        expect(timezone?.failures.map((failure) => failure.code)).toEqual(['unreachable', 'not-success']);
        expect(stderr.text()).toContain('source: system');
        expect(stderr.text()).toContain('relay-exit-ip failed: unreachable');
        expect(stderr.text()).toContain('proxy-host failed: not-success');
    });
});

describe('browser.launch startup log names the timezone source', () => {
    /** A temporary session leaves a running browser: track it so afterEach closes it. */
    async function launchTemporary(): Promise<{ ok: boolean; data?: unknown }> {
        const result = await runCommand(ctx, command('browser.launch'), { proxy: PROXY_URL });
        if (result.ok) {
            const { profileId } = result.data as { profileId: string };
            opened = ctx.profiles.getRunning().get(profileId) ?? null;
        }
        return result;
    }

    it('reports the relay exit location for a temporary session', async () => {
        await startRelay(GEO_SUCCESS);
        const stderr = captureStderr();
        let result: { ok: boolean; data?: unknown };
        try {
            result = await launchTemporary();
        } finally {
            stderr.restore();
        }
        expect(result.ok).toBe(true);
        const launched = command('browser.launch').render(result.data);
        expect(launched).toContain('timezone:   America/New_York (source: relay-exit-ip, Verona, United States)');
        expect(stderr.text()).toContain('source: relay-exit-ip, Verona, United States');
    });

    it('keeps the failure reason when the proxy host lookup is the only success', async () => {
        await deadRelay();
        stubHostLookup(GEO_SUCCESS);
        const stderr = captureStderr();
        let result: { ok: boolean; data?: unknown };
        try {
            result = await launchTemporary();
        } finally {
            stderr.restore();
        }

        expect(result.ok).toBe(true);
        expect(stderr.text()).toContain('source: proxy-host, Verona, United States');
        expect(stderr.text()).toContain('relay-exit-ip failed: unreachable');
    });
});
