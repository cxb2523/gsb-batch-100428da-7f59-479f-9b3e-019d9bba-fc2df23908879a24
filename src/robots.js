// RobotsCache: fetches a target origin's robots.txt, caches it (TTL + size-bounded),
// and decides whether a given URL is allowed for a given user-agent.
//
// Status policy (per the agreed fail-mode):
//   2xx  -> parse the body and apply the rules
//   4xx  -> "allow all"  (missing/forbidden robots.txt = no restrictions, per the spec)
//   5xx / network error / timeout -> "block all"
//
// When a fetch fails closed, the cache entry records *why* in failureCategory:
//   'http-5xx'      : origin responded with a 5xx (or other non-4xx error) status
//   'timeout'       : the fetch was aborted by our timeout (AbortError)
//   'network-error' : everything else thrown by fetch (DNS, connection reset, …)
// Successful fetches and manual overrides carry no failure category.
import { RobotsMatcher } from 'google-robotstxt-parser';

export class RobotsCache {
  /**
   * @param {object} opts
   * @param {object} opts.config  resolved config (cacheTtlMs, cacheMax, robotsTimeoutMs, robotsUa)
   * @param {object} [opts.logger]
   * @param {Function} [opts.fetchImpl]  fetch-compatible function (injectable for tests)
   */
  constructor({ config, logger, fetchImpl } = {}) {
    this.config = config;
    this.logger = logger;
    this.fetch = fetchImpl || globalThis.fetch;
    /** @type {Map<string, {policy: string, body: string, expires: number, failureCategory: string|null}>} */
    this.cache = new Map();
    /** @type {Map<string, Promise>} in-flight fetches, deduped by origin */
    this.pending = new Map();
  }

  /**
   * @param {string} targetUrl  the full URL the client wants to reach
   * @param {string|null} requestUa  the client's User-Agent (used unless ROBOTS_UA is set)
   * @returns {Promise<{allowed: boolean, reason: string, line: number, failureCategory: string|null}>}
   */
  async decide(targetUrl, requestUa) {
    const url = new URL(targetUrl);
    const origin = url.origin;
    const ua = this.config.robotsUa || requestUa || '*';

    const entry = await this._getEntry(origin);

    if (entry.policy === 'allow') {
      return { allowed: true, reason: 'robots-absent', line: 0, failureCategory: null };
    }
    if (entry.policy === 'block') {
      return {
        allowed: false,
        reason: 'robots-unavailable',
        line: 0,
        failureCategory: entry.failureCategory ?? null,
      };
    }

    // policy === 'parse'
    const matcher = new RobotsMatcher();
    const allowed = matcher.oneAgentAllowedByRobots(entry.body, ua, targetUrl);
    return {
      allowed,
      reason: allowed ? 'robots-allow' : 'robots-disallow',
      line: matcher.matchingLine(),
      failureCategory: null,
    };
  }

  async _getEntry(origin) {
    const cached = this.cache.get(origin);
    if (cached && cached.expires > this._now()) return cached;

    if (this.pending.has(origin)) return this.pending.get(origin);

    const p = this._fetchEntry(origin).finally(() => this.pending.delete(origin));
    this.pending.set(origin, p);
    return p;
  }

  async _fetchEntry(origin) {
    const robotsUrl = `${origin}/robots.txt`;
    let entry;
    let ttlMs = this.config.cacheTtlMs;
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.config.robotsTimeoutMs);
      let res;
      try {
        res = await this.fetch(robotsUrl, {
          signal: controller.signal,
          redirect: 'follow',
          headers: { 'user-agent': 'robotstxt-proxy' },
        });
      } finally {
        clearTimeout(timer);
      }

      // Respect Cache-Control: max-age from the robots.txt response, capped at config ceiling.
      const cc = (res.headers?.get?.('cache-control') || '').toLowerCase();
      const maxAgeMatch = /max-age\s*=\s*(\d+)/.exec(cc);
      if (maxAgeMatch) {
        ttlMs = Math.min(parseInt(maxAgeMatch[1], 10) * 1000, this.config.cacheTtlMs);
      }

      if (res.ok) {
        const body = await res.text();
        entry = { policy: 'parse', body, failureCategory: null };
      } else if (res.status >= 400 && res.status < 500) {
        entry = { policy: 'allow', body: '', failureCategory: null };
      } else {
        // 5xx (or any other error status) -> fail closed, tagged with the failure class.
        entry = { policy: 'block', body: '', failureCategory: 'http-5xx' };
      }
      this.logger?.debug('robots fetched', { origin, status: res.status, policy: entry.policy });
    } catch (err) {
      // Network error, timeout/abort, DNS failure, etc. -> fail closed.
      // AbortError is what our AbortController raises when the timeout fires; an abort from
      // any other source still means the fetch did not complete, so it is classed the same way.
      const failureCategory = err?.name === 'AbortError' ? 'timeout' : 'network-error';
      entry = { policy: 'block', body: '', failureCategory };
      this.logger?.warn('robots fetch failed', { origin, error: err.message, failureCategory });
    }

    entry.expires = this._now() + ttlMs;
    entry.ttlMs = ttlMs;
    entry.isOverride = false;
    this._set(origin, entry);
    return entry;
  }

  /** Returns a snapshot of all cache entries for the dashboard UI. */
  getAll() {
    const now = this._now();
    return [...this.cache.entries()].map(([origin, e]) => {
      const snapshot = {
        origin,
        policy: e.policy,
        expiresAt: e.expires,
        ttlMs: e.ttlMs ?? this.config.cacheTtlMs,
        isOverride: e.isOverride ?? false,
        remainingMs: Math.max(0, e.expires - now),
        body: e.body ?? '',
      };
      // Manual overrides never result from a fetch, so they carry no failure information.
      if (!snapshot.isOverride) snapshot.failureCategory = e.failureCategory ?? null;
      return snapshot;
    });
  }

  /** Removes an origin from the cache; the next request will re-fetch. */
  remove(origin) {
    this.cache.delete(origin);
  }

  /** Manually pins a robots.txt body for an origin without a network fetch. */
  override(origin, body) {
    const entry = {
      policy: 'parse',
      body,
      expires: this._now() + this.config.cacheTtlMs,
      ttlMs: this.config.cacheTtlMs,
      isOverride: true,
    };
    if (this.cache.has(origin)) this.cache.delete(origin);
    this.cache.set(origin, entry);
    while (this.cache.size > this.config.cacheMax) {
      this.cache.delete(this.cache.keys().next().value);
    }
  }

  _set(origin, entry) {
    // Refresh insertion order so eviction is least-recently-set.
    if (this.cache.has(origin)) this.cache.delete(origin);
    this.cache.set(origin, entry);
    while (this.cache.size > this.config.cacheMax) {
      const oldest = this.cache.keys().next().value;
      this.cache.delete(oldest);
    }
  }

  _now() {
    return Date.now();
  }
}
