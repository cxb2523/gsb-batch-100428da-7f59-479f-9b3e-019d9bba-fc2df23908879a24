// ============================================================================
// @aitofy/browser-profiles - Proxy relay and IP-based timezone detection
// ============================================================================

import http from 'http';
import type { ProxyConfig, StoredProfile, TimezoneFailure, TimezoneResolution } from '../types';
import { createLogger } from '../log';
import type { Logger } from '../log';
import { loadProxyChain } from './deps';

const log = createLogger('launcher-proxy');

/** Timezone used when neither the profile nor the proxy IP tells us one. */
export const FALLBACK_TIMEZONE = 'America/New_York';

/** Matched by the command layer to report a configuration mistake, not a launch failure. */
export const DETACHED_PROXY_MESSAGE =
    'detached=true cannot be combined with an authenticated proxy: Chrome cannot send the credentials ' +
    'itself, and the local relay that does dies with this process, leaving the browser unable to reach ' +
    'the network. Drop detached, or use a proxy without a username and password.';

/**
 * Fail before Chrome exists rather than leave a browser that can never load a page.
 * @throws when a detached launch would outlive the relay it depends on.
 */
export function assertDetachedLaunchAllowed(proxy: ProxyConfig | null | undefined, detached: boolean): void {
    if (detached && proxy?.username) throw new Error(DETACHED_PROXY_MESSAGE);
}

/**
 * Build the upstream proxy URL, credentials included.
 * Chrome cannot authenticate itself, so this URL is only ever handed to the relay.
 */
export function buildProxyUrl(proxy: ProxyConfig): string {
    const { type, host, port, username, password } = proxy;
    const protocol = type === 'socks5' ? 'socks5' : 'http';

    if (username && password) {
        return `${protocol}://${encodeURIComponent(username)}:${encodeURIComponent(password)}@${host}:${port}`;
    }

    return `${protocol}://${host}:${port}`;
}

export interface GeoLocation {
    timezone: string;
    country: string;
    city: string;
    region: string;
}

/** One GeoIP lookup: either the location, or why there is none. */
export type GeoLookupResult =
    | { ok: true; geo: GeoLocation }
    | { ok: false; failure: TimezoneFailure };

/** The step a failed lookup belongs to. */
type LookupStep = TimezoneFailure['step'];

function lookupFailure(step: LookupStep, code: TimezoneFailure['code'], detail: string): GeoLookupResult {
    return { ok: false, failure: { step, code, detail } };
}

const GEO_FIELDS = 'status,country,regionName,city,timezone';

/** Keep failure text short: an HTML error page is not useful in a log line. */
function previewAnswer(body: string): string {
    const flat = body.replace(/\s+/g, ' ').trim();
    if (!flat) return '(empty answer)';
    return flat.length > 80 ? `${flat.slice(0, 80)}...` : flat;
}

function errorDetail(error: unknown): string {
    const code = (error as NodeJS.ErrnoException)?.code;
    if (code) return code;
    return error instanceof Error ? error.message : String(error);
}

/** Turn an ip-api.com body into a location, or into the reason it gave none. */
function parseGeoResult(body: string, step: LookupStep): GeoLookupResult {
    let data: {
        status?: unknown;
        country?: unknown;
        regionName?: unknown;
        city?: unknown;
        timezone?: unknown;
    };
    try {
        data = JSON.parse(body) as typeof data;
    } catch {
        return lookupFailure(step, 'invalid-json', `answer is not JSON: ${previewAnswer(body)}`);
    }

    if (typeof data !== 'object' || data === null) {
        return lookupFailure(step, 'invalid-json', `answer is not a JSON object: ${previewAnswer(body)}`);
    }
    if (data.status !== 'success') {
        const status = typeof data.status === 'string' ? data.status : '(missing)';
        return lookupFailure(step, 'not-success', `ip-api.com answered status=${status}`);
    }
    if (typeof data.timezone !== 'string' || !data.timezone) {
        return lookupFailure(step, 'missing-timezone', 'ip-api.com answered success without a timezone');
    }

    return {
        ok: true,
        geo: {
            timezone: data.timezone,
            country: typeof data.country === 'string' ? data.country : '',
            city: typeof data.city === 'string' ? data.city : '',
            region: typeof data.regionName === 'string' ? data.regionName : '',
        },
    };
}

/**
 * Look up a timezone for an IP with a free GeoIP API (directly, not through a proxy).
 * @returns Geo info, or null when the lookup fails.
 */
export async function detectTimezoneFromIP(ip: string): Promise<GeoLocation | null> {
    const result = await lookupHostTimezone(ip);
    return result.ok ? result.geo : null;
}

/** Direct GeoIP lookup for the proxy host, keeping the reason when it fails. */
async function lookupHostTimezone(host: string, timeoutMs = 10_000): Promise<GeoLookupResult> {
    let response: Response;
    try {
        // ip-api.com: free, no API key, 45 requests/minute.
        response = await fetch(`http://ip-api.com/json/${host}?fields=${GEO_FIELDS}`, {
            signal: AbortSignal.timeout(timeoutMs),
        });
    } catch (error) {
        const name = (error as Error)?.name;
        if (name === 'TimeoutError' || name === 'AbortError') {
            return lookupFailure('proxy-host', 'timeout', `ip-api.com did not answer within ${timeoutMs} ms`);
        }
        return lookupFailure('proxy-host', 'unreachable', `could not reach ip-api.com: ${errorDetail(error)}`);
    }

    if (!response.ok) {
        return lookupFailure('proxy-host', 'http-error', `ip-api.com answered HTTP ${response.status}`);
    }
    try {
        return parseGeoResult(await response.text(), 'proxy-host');
    } catch (error) {
        return lookupFailure('proxy-host', 'unreachable', `ip-api.com answer was cut off: ${errorDetail(error)}`);
    }
}

/**
 * Locate the IP a site actually sees by asking through the relay. A gateway proxy's
 * host is not its exit IP, so looking the host up can return another country.
 * @returns The location or the reason the probe gave none. Never rejects.
 */
export function probeExitLocation(relayUrl: string, timeoutMs = 10_000): Promise<GeoLookupResult> {
    let relay: URL;
    try {
        relay = new URL(relayUrl);
    } catch {
        return Promise.resolve(
            lookupFailure('relay-exit-ip', 'unreachable', `relay URL "${relayUrl}" is not a valid URL`)
        );
    }

    return new Promise((resolve) => {
        let settled = false;
        let timedOut = false;
        const finish = (result: GeoLookupResult): void => {
            if (settled) return;
            settled = true;
            resolve(result);
        };
        const timeoutFailure = (): GeoLookupResult => lookupFailure(
            'relay-exit-ip',
            'timeout',
            `no answer through the relay within ${timeoutMs} ms`
        );

        const request = http.get({
            host: relay.hostname,
            port: relay.port,
            path: `http://ip-api.com/json/?fields=${GEO_FIELDS}`,
            headers: { host: 'ip-api.com' },
            timeout: timeoutMs,
        }, (response) => {
            if (response.statusCode && response.statusCode >= 400) {
                response.resume();
                finish(lookupFailure(
                    'relay-exit-ip',
                    'http-error',
                    `the relay reached ip-api.com, which answered HTTP ${response.statusCode}`
                ));
                return;
            }

            let body = '';
            response.setEncoding('utf8');
            response.on('data', (chunk: string) => { body += chunk; });
            response.on('error', (error) => {
                finish(timedOut ? timeoutFailure() : lookupFailure(
                    'relay-exit-ip', 'unreachable', `relay answer was cut off: ${errorDetail(error)}`
                ));
            });
            response.on('end', () => {
                finish(timedOut ? timeoutFailure() : parseGeoResult(body, 'relay-exit-ip'));
            });
        });
        request.on('timeout', () => {
            timedOut = true;
            request.destroy();
            finish(timeoutFailure());
        });
        request.on('error', (error) => {
            finish(timedOut ? timeoutFailure() : lookupFailure(
                'relay-exit-ip', 'unreachable', `relay did not answer: ${errorDetail(error)}`
            ));
        });
    });
}

/**
 * Locate the IP a site actually sees by asking through the relay.
 * @returns Geo info, or null when the lookup fails.
 */
export async function detectExitLocation(relayUrl: string, timeoutMs = 10_000): Promise<GeoLocation | null> {
    const result = await probeExitLocation(relayUrl, timeoutMs);
    return result.ok ? result.geo : null;
}

/** Timezone of the proxy exit node, or the fallback when it cannot be resolved. */
export async function autoDetectTimezone(proxy: ProxyConfig): Promise<string> {
    const result = await lookupHostTimezone(proxy.host);
    if (result.ok) {
        log.info(`Detected location: ${result.geo.city}, ${result.geo.country} (${result.geo.timezone})`);
        return result.geo.timezone;
    }
    log.debug(`Proxy host lookup failed (${result.failure.code}): ${result.failure.detail}`);
    return FALLBACK_TIMEZONE;
}

/** Hooks the resolver takes, injectable so a test never has to reach the real network. */
export interface ResolveTimezoneOptions {
    /** Timeout for the relay probe. */
    relayTimeoutMs?: number;
    /** Relay probe, default `probeExitLocation`. */
    probeExit?: (relayUrl: string, timeoutMs: number) => Promise<GeoLookupResult>;
    /** Proxy-host lookup, default the direct GeoIP request. */
    lookupHost?: (host: string) => Promise<GeoLookupResult>;
    /** This machine's clock, default `Intl.DateTimeFormat().resolvedOptions().timeZone`. */
    systemTimezone?: () => string;
}

function locationDetail(geo: GeoLocation): string {
    return [geo.city, geo.country].filter(Boolean).join(', ') || geo.timezone;
}

/**
 * Run one lookup, turning an unexpected throw into a failure reason. Resolution must
 * never propagate: a timezone problem may fall back, but it may not fail the launch.
 */
async function settleLookup(
    step: LookupStep,
    run: () => Promise<GeoLookupResult>
): Promise<GeoLookupResult> {
    try {
        return await run();
    } catch (error) {
        return lookupFailure(step, 'unreachable', `the lookup threw: ${errorDetail(error)}`);
    }
}

/**
 * Timezone for Chrome's TZ, from the profile, else the proxy exit IP, else this host.
 * The value follows the same order as before; the result also says where it came from
 * and why earlier lookups failed, so a fallback is never silent.
 */
export async function resolveTimezone(
    profile: StoredProfile,
    relayUrl: string | undefined,
    options: ResolveTimezoneOptions = {}
): Promise<TimezoneResolution> {
    if (profile.timezone) {
        return { timezone: profile.timezone, source: 'profile', failures: [] };
    }

    const failures: TimezoneFailure[] = [];
    const probeExit = options.probeExit ?? probeExitLocation;
    const lookupHost = options.lookupHost ?? lookupHostTimezone;
    const relayTimeout = options.relayTimeoutMs ?? 10_000;

    const proxy = profile.proxy;
    if (proxy) {
        if (relayUrl) {
            const exit = await settleLookup('relay-exit-ip', () => probeExit(relayUrl, relayTimeout));
            if (exit.ok) {
                return {
                    timezone: exit.geo.timezone,
                    source: 'relay-exit-ip',
                    detail: locationDetail(exit.geo),
                    failures,
                };
            }
            failures.push(exit.failure);
        } else {
            // Verbatim what the old resolver did: no relay, so the host is asked directly.
            failures.push({
                step: 'relay-exit-ip',
                code: 'no-relay',
                detail: 'the relay URL was not available, so the exit IP could not be probed',
            });
        }

        const host = await settleLookup('proxy-host', () => lookupHost(proxy.host));
        if (host.ok) {
            return {
                timezone: host.geo.timezone,
                source: 'proxy-host',
                detail: locationDetail(host.geo),
                failures,
            };
        }
        failures.push(host.failure);
    }

    const systemTimezone = (options.systemTimezone ?? (() => Intl.DateTimeFormat().resolvedOptions().timeZone))();
    return { timezone: systemTimezone, source: 'system', failures };
}

function failureSummary(failure: TimezoneFailure): string {
    return `${failure.step} failed: ${failure.code} (${failure.detail})`;
}

/** `America/New_York (source: relay-exit-ip, Verona, United States)` plus any failed lookups. */
export function describeTimezoneResolution(resolution: TimezoneResolution): string {
    const origin = resolution.detail
        ? `${resolution.source}, ${resolution.detail}`
        : resolution.source;
    const reasons = resolution.failures.length > 0
        ? `; ${resolution.failures.map(failureSummary).join('; ')}`
        : '';
    return `${resolution.timezone} (source: ${origin}${reasons})`;
}

/** One line that says where the timezone came from and what had to fail to get there. */
export function formatTimezoneResolution(resolution: TimezoneResolution): string {
    return `Timezone for Chrome: ${describeTimezoneResolution(resolution)}`;
}

/**
 * Print the resolution at startup: the summary at info level, and every failed
 * lookup at warn level so the operator sees it without --verbose.
 */
export function logTimezoneResolution(resolution: TimezoneResolution, logger: Logger = log): void {
    const line = formatTimezoneResolution(resolution);
    if (resolution.failures.length > 0) {
        logger.warn(line);
        for (const failure of resolution.failures) {
            logger.warn(`Timezone lookup ${failure.step} failed: ${failure.code} - ${failure.detail}`);
        }
    } else {
        logger.info(line);
    }
}

/**
 * Start a local relay that answers the proxy challenge for Chrome.
 * @returns The local URL to hand to `--proxy-server`.
 */
export async function startProxyRelay(proxy: ProxyConfig): Promise<string> {
    const proxyChain = await loadProxyChain();
    try {
        return await proxyChain.anonymizeProxy(buildProxyUrl(proxy));
    } catch (error) {
        log.error('Proxy setup failed', error);
        throw new Error(`Failed to configure proxy: ${(error as Error).message}`);
    }
}

/** Tear down a relay started by `startProxyRelay`. Never throws. */
export async function closeProxyRelay(relayUrl: string | undefined): Promise<void> {
    if (!relayUrl) return;
    try {
        const proxyChain = await loadProxyChain();
        await proxyChain.closeAnonymizedProxy(relayUrl, true);
    } catch (error) {
        // The relay dies with the process that created it; failing here is expected.
        log.debug('Could not close proxy relay', error);
    }
}
