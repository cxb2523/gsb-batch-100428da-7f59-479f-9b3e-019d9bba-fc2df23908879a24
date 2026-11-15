// Local tests for userscript.js — no real TikTok pages involved.
// The userscript runs inside a vm context with a fake browser environment:
// self-made HTML snippets, a controllable fetch stub, captured alert/console.
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const NODE = process.execPath;
const SCRIPT_SRC = fs.readFileSync(path.join(__dirname, '..', 'userscript.js'), 'utf8');

// ---------- Self-made HTML snippets ----------

function jsonScript(id, obj) {
    return '<script id="' + id + '" type="application/json">' + JSON.stringify(obj) + '</script>';
}

// Page whose embedded JSON carries both creator and viewer region.
const HTML_WITH_DATA =
    '<html><head>' +
    jsonScript('__UNIVERSAL_DATA_FOR_REHYDRATION__', {
        '__DEFAULT_SCOPE__': {
            'webapp.app-context': { region: 'US', language: 'en' },
            'webapp.video-detail': { itemInfo: { itemStruct: { id: '%VID%', locationCreated: 'DE' } } }
        }
    }) +
    '</head><body>fake page</body></html>';

// Page with creator region but no viewer region anywhere.
const HTML_NO_VIEWER =
    '<html><head>' +
    jsonScript('SIGI_STATE', { ItemModule: { x: { id: '%VID%', locationCreated: 'FR' } } }) +
    '</head><body></body></html>';

// Page with no region data at all (login wall etc.).
const HTML_NO_DATA = '<html><head><script>var x = 1;</script></head><body>login wall</body></html>';

// Re-fetched page (fallback) that does contain the data.
const HTML_FALLBACK_WITH_DATA =
    '<html><head>' +
    jsonScript('__UNIVERSAL_DATA_FOR_REHYDRATION__', {
        a: { 'webapp.app-context': { region: 'BR' } },
        b: { item: { id: '%VID%', locationCreated: 'JP' } }
    }) +
    '</head><body></body></html>';

function htmlFor(template, videoId) {
    return template.split('%VID%').join(videoId);
}

// ---------- Fake browser environment ----------

function parseScripts(html) {
    const scripts = [];
    const re = /<script([^>]*)>([\s\S]*?)<\/script>/gi;
    let m;
    while ((m = re.exec(html))) {
        const attrs = m[1] || '';
        const id = (attrs.match(/id="([^"]+)"/) || [])[1] || null;
        const type = (attrs.match(/type="([^"]+)"/) || [])[1] || null;
        scripts.push({ id, type, textContent: m[2] });
    }
    return scripts;
}

function queryFrom(scripts, sel) {
    if (sel === 'script') return scripts;
    // the JSON-script selector used by the userscript
    return scripts.filter(s =>
        s.id === '__UNIVERSAL_DATA_FOR_REHYDRATION__' ||
        s.id === 'SIGI_STATE' ||
        s.id === '__NEXT_DATA__' ||
        s.type === 'application/json');
}

function makeEnv(opts) {
    const env = {
        alerts: [],
        logs: [],
        errors: [],
        fetchCalls: [],
        timers: [],       // pending setTimeout callbacks, pumped manually
        listeners: {},    // window event listeners
    };

    let currentScripts = parseScripts(opts.html || '<html></html>');
    env.setPage = (html) => { currentScripts = parseScripts(html); };

    const loc = {};
    env.setUrl = (url) => {
        const u = new URL(url, loc.href || 'https://www.tiktok.com/');
        loc.href = u.href;
        loc.pathname = u.pathname;
    };
    env.setUrl(opts.url);

    const documentShim = {
        readyState: 'complete',
        querySelectorAll(sel) { return queryFrom(currentScripts, sel); },
    };

    const sandbox = {
        console: {
            log: (...a) => env.logs.push(a.map(String).join(' ')),
            error: (...a) => env.errors.push(a.map(String).join(' ')),
        },
        alert: (msg) => env.alerts.push(String(msg)),
        fetch: (...a) => {
            env.fetchCalls.push(a);
            return opts.fetchImpl(...a);
        },
        DOMParser: class {
            parseFromString(html) {
                const scripts = parseScripts(html);
                return { querySelectorAll: (sel) => queryFrom(scripts, sel) };
            }
        },
        location: loc,
        document: documentShim,
        history: {
            pushState(s, t, url) { if (url) env.setUrl(url); },
            replaceState(s, t, url) { if (url) env.setUrl(url); },
        },
        setTimeout: (fn) => { env.timers.push(fn); return env.timers.length; },
        clearTimeout: () => {},
        setInterval: () => 0,
        clearInterval: () => {},
        Intl, Promise, JSON, Math, Date,
        String, Number, Boolean, Array, Object, RegExp, Error, Set, Map,
    };
    sandbox.window = sandbox;
    sandbox.window.top = sandbox.window;
    sandbox.window.self = sandbox.window;
    sandbox.window.addEventListener = (ev, fn) => {
        (env.listeners[ev] = env.listeners[ev] || []).push(fn);
    };
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    env.sandbox = sandbox;

    // Navigate like the SPA would: swap page content + URL, then pushState.
    env.navigate = (url, html) => {
        env.setPage(html);
        sandbox.history.pushState(null, '', url);
    };
    // Fire a navigation event manually (popstate path).
    env.triggerPopstate = () => (env.listeners.popstate || []).forEach(f => f());

    // Pump queued timers; n = how many to run (default: all currently queued).
    env.pump = (n) => {
        let count = 0;
        while (env.timers.length && (n === undefined || count < n)) {
            env.timers.shift()();
            count++;
        }
    };
    // Let queued microtasks (promise continuations) settle.
    env.flush = async (rounds = 30) => {
        for (let i = 0; i < rounds; i++) await Promise.resolve();
    };
    // Pump one timer, then let microtasks settle (one retry step).
    env.step = async () => { env.pump(1); await env.flush(); };
    // Alternate pumping and flushing until no timers remain.
    env.settle = async () => {
        for (let i = 0; i < 200 && env.timers.length; i++) await env.step();
        await env.flush();
    };
    // Run the userscript in this environment.
    env.start = () => vm.runInContext(SCRIPT_SRC, env.sandbox, { filename: 'userscript.js' });

    return env;
}

// ---------- Tiny test runner ----------

const tests = [];
function test(name, fn) { tests.push({ name, fn }); }
function assert(cond, msg) { if (!cond) throw new Error('assertion failed: ' + msg); }
function assertIncludes(haystack, needle, msg) {
    assert(haystack.includes(needle), (msg || 'missing substring') + '\n  wanted: ' + JSON.stringify(needle) + '\n  in: ' + JSON.stringify(haystack));
}

// ---------- Tests ----------

test('success: shows creator and viewer region from embedded JSON', async () => {
    const env = makeEnv({
        url: 'https://www.tiktok.com/@user/video/111',
        html: htmlFor(HTML_WITH_DATA, '111'),
        fetchImpl: async () => { throw new Error('fetch should not be called'); },
    });
    env.start();
    await env.flush();
    assert(env.alerts.length === 1, 'expected exactly one alert, got ' + env.alerts.length);
    assertIncludes(env.alerts[0], 'Creator region: DE', 'creator region');
    assertIncludes(env.alerts[0], 'Germany', 'creator region name');
    assertIncludes(env.alerts[0], 'Viewer region: US', 'viewer region');
    assert(env.fetchCalls.length === 0, 'fetch must not be called when page data suffices');
});

test('success: missing viewer region shows N/A', async () => {
    const env = makeEnv({
        url: 'https://www.tiktok.com/@user/video/222',
        html: htmlFor(HTML_NO_VIEWER, '222'),
        fetchImpl: async () => { throw new Error('fetch should not be called'); },
    });
    env.start();
    await env.flush();
    assert(env.alerts.length === 1, 'expected one alert');
    assertIncludes(env.alerts[0], 'Creator region: FR', 'creator region');
    assertIncludes(env.alerts[0], 'Viewer region: N/A', 'viewer N/A');
});

test('retry: logs current attempt number and video ID while waiting', async () => {
    const env = makeEnv({
        url: 'https://www.tiktok.com/@user/video/333',
        html: HTML_NO_DATA,
        fetchImpl: async () => ({ ok: true, text: async () => htmlFor(HTML_FALLBACK_WITH_DATA, '333') }),
    });
    env.start();
    await env.settle();
    // 10 attempts, 9 waits -> 9 progress log lines, all tagged with the video ID
    const progress = env.logs.filter(l => l.includes('attempt'));
    assert(progress.length === 9, 'expected 9 retry progress lines, got ' + progress.length + ': ' + JSON.stringify(env.logs));
    assertIncludes(progress[0], 'attempt 1/10', 'first attempt number');
    assertIncludes(progress[8], 'attempt 9/10', 'last attempt number');
    progress.forEach(l => assertIncludes(l, '333', 'video ID in progress line'));
    // fallback then succeeds
    assert(env.alerts.length === 1, 'expected one alert after fallback, got ' + env.alerts.length);
    assertIncludes(env.alerts[0], 'Creator region: JP', 'fallback creator region');
    assertIncludes(env.alerts[0], 'Viewer region: BR', 'fallback viewer region');
});

test('stale: result for old video is discarded after switching videos (no alert, no log)', async () => {
    // fetch stays pending until the test resolves it, so 444's fallback
    // response arrives only after the page has moved on to 555.
    let resolveFetch;
    const fetchPromise = new Promise(r => { resolveFetch = r; });
    const env = makeEnv({
        url: 'https://www.tiktok.com/@user/video/444',
        html: HTML_NO_DATA, // video 444 has no data -> retries, then fallback fetch
        fetchImpl: () => fetchPromise,
    });
    env.start();
    await env.flush();
    assert(env.logs.some(l => l.includes('444')), 'collection for 444 should have started');

    // Let 444's retries run out so its fallback fetch is in flight...
    for (let i = 0; i < 9; i++) await env.step();
    assert(env.fetchCalls.length === 1, 'fallback fetch for 444 should have fired once, got ' + env.fetchCalls.length);

    // ...then switch to video 555 (which has data inline)...
    env.navigate('https://www.tiktok.com/@user/video/555', htmlFor(HTML_WITH_DATA, '555'));
    // ...and only now does 444's fallback response arrive.
    resolveFetch({ ok: true, text: async () => htmlFor(HTML_FALLBACK_WITH_DATA, '444') });
    await env.settle();

    // 444's fetched result must be discarded: exactly one alert, and it is 555's.
    assert(env.alerts.length === 1, 'expected exactly one alert (555 only), got ' + env.alerts.length + ': ' + JSON.stringify(env.alerts));
    assertIncludes(env.alerts[0], 'Creator region: DE', 'alert belongs to video 555');
    assert(!env.alerts[0].includes('JP'), 'stale result for 444 must not be alerted');
    // no success log for the stale video either (only retry-progress lines may mention 444)
    const successLogs = env.logs.filter(l => !l.includes('attempt'));
    assert(successLogs.every(l => !l.includes('JP') && !l.includes('BR')),
        'stale result must not be logged: ' + JSON.stringify(successLogs));
});

test('stale: switching videos during the retry phase abandons the old collection', async () => {
    const env = makeEnv({
        url: 'https://www.tiktok.com/@user/video/448',
        html: HTML_NO_DATA,
        fetchImpl: async () => ({ ok: true, text: async () => htmlFor(HTML_FALLBACK_WITH_DATA, '448') }),
    });
    env.start();
    await env.flush();
    // switch away after the first failed attempt, while retries are pending
    env.navigate('https://www.tiktok.com/@user/video/449', htmlFor(HTML_WITH_DATA, '449'));
    await env.settle();
    assert(env.fetchCalls.length === 0, 'fallback fetch for the abandoned video must not fire');
    assert(env.alerts.length === 1, 'expected exactly one alert (449 only), got ' + env.alerts.length);
    assertIncludes(env.alerts[0], 'Creator region: DE', 'alert belongs to video 449');
});

test('dedup: repeated triggers for the same video do not re-collect', async () => {
    const env = makeEnv({
        url: 'https://www.tiktok.com/@user/video/666',
        html: htmlFor(HTML_WITH_DATA, '666'),
        fetchImpl: async () => { throw new Error('fetch should not be called'); },
    });
    env.start();
    await env.flush();
    // re-trigger several times via popstate and same-URL pushState
    env.triggerPopstate();
    env.triggerPopstate();
    env.navigate('https://www.tiktok.com/@user/video/666', htmlFor(HTML_WITH_DATA, '666'));
    await env.settle();
    assert(env.alerts.length === 1, 'expected exactly one alert for repeated triggers, got ' + env.alerts.length);
});

test('dedup: concurrent triggers while a collection is in flight do not duplicate', async () => {
    const env = makeEnv({
        url: 'https://www.tiktok.com/@user/video/777',
        html: HTML_NO_DATA,
        fetchImpl: async () => ({ ok: true, text: async () => htmlFor(HTML_FALLBACK_WITH_DATA, '777') }),
    });
    env.start();
    await env.flush();
    // collection for 777 is now waiting between retries; trigger again
    env.triggerPopstate();
    env.navigate('https://www.tiktok.com/@user/video/777', HTML_NO_DATA);
    await env.settle();
    assert(env.fetchCalls.length === 1, 'fallback fetch must fire exactly once, got ' + env.fetchCalls.length);
    assert(env.alerts.length === 1, 'expected exactly one alert, got ' + env.alerts.length);
});

test('fallback failure: HTTP status is reported as a fallback-request failure', async () => {
    const env = makeEnv({
        url: 'https://www.tiktok.com/@user/video/888',
        html: HTML_NO_DATA,
        fetchImpl: async () => ({ ok: false, status: 503 }),
    });
    env.start();
    await env.settle();
    assert(env.alerts.length === 1, 'expected one alert');
    assertIncludes(env.alerts[0], 'fallback request', 'must blame the fallback request');
    assertIncludes(env.alerts[0], 'HTTP 503', 'must include the HTTP status');
    assert(!env.alerts[0].includes('missing from the page data'), 'must not be confused with missing page data');
});

test('fallback failure: network error message is reported', async () => {
    const env = makeEnv({
        url: 'https://www.tiktok.com/@user/video/889',
        html: HTML_NO_DATA,
        fetchImpl: async () => { throw new Error('net::ERR_CONNECTION_RESET'); },
    });
    env.start();
    await env.settle();
    assert(env.alerts.length === 1, 'expected one alert');
    assertIncludes(env.alerts[0], 'fallback request', 'must blame the fallback request');
    assertIncludes(env.alerts[0], 'net::ERR_CONNECTION_RESET', 'must include the error message');
});

test('missing data: fetched page without locationCreated is reported as missing page data', async () => {
    const env = makeEnv({
        url: 'https://www.tiktok.com/@user/video/890',
        html: HTML_NO_DATA,
        fetchImpl: async () => ({ ok: true, text: async () => HTML_NO_DATA }),
    });
    env.start();
    await env.settle();
    assert(env.alerts.length === 1, 'expected one alert');
    assertIncludes(env.alerts[0], 'locationCreated', 'must name the missing field');
    assertIncludes(env.alerts[0], 'missing from the page data', 'must blame missing page data');
    assert(!env.alerts[0].includes('fallback request'), 'must not be confused with a fallback failure');
});

// ---------- Run ----------

(async () => {
    let failed = 0;
    for (const t of tests) {
        try {
            await t.fn();
            console.log('PASS  ' + t.name);
        } catch (e) {
            failed++;
            console.error('FAIL  ' + t.name + '\n      ' + e.message);
        }
    }
    console.log(failed ? ('\n' + failed + ' test(s) failed') : '\nAll ' + tests.length + ' tests passed');
    process.exit(failed ? 1 : 0);
})();
