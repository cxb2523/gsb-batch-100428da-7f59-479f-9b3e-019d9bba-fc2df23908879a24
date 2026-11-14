import { URL } from 'url';
import setCookieParser from 'set-cookie-parser';
import logger from './logger.js';

/**
 * Parses a request Cookie header ("name1=value1; name2=value2")
 * into a list of {name, value} pairs.
 */
function parseCookieHeader (cookieHeader) {
	return cookieHeader.split(';')
		.map(pair => {
			const separatorIndex = pair.indexOf('=');
			if (separatorIndex === -1) {
				return null;
			}
			return {
				name: pair.slice(0, separatorIndex).trim(),
				value: pair.slice(separatorIndex + 1).trim()
			};
		})
		.filter(cookie => cookie && cookie.name);
}

/**
 * Parses a response Set-Cookie header into a puppeteer CookieData object.
 * Attributes which don't affect scraping (SameSite, Priority, ...) are ignored.
 * Returns null for malformed values.
 */
function parseSetCookieHeader (setCookieHeader, requestUrl) {
	// decodeValues: false keeps values verbatim, so they round-trip
	// into the browser jar and back into Cookie headers unchanged
	const parsed = setCookieParser.parseString(setCookieHeader, { decodeValues: false });
	if (!parsed.name) {
		return null;
	}

	const url = new URL(requestUrl);
	if (parsed.domain && !domainMatches(url.hostname, parsed.domain)) {
		// like browsers do (RFC 6265 5.3 step 6): a response must not set cookies for other domains
		logger.debug(`ignoring cookie ${parsed.name} for ${parsed.domain} set by ${url.hostname}`);
		return null;
	}

	const cookie = {
		name: parsed.name,
		value: parsed.value,
		// a Domain attribute always makes the cookie available to subdomains
		domain: parsed.domain ? ensureLeadingDot(parsed.domain) : url.hostname,
		path: parsed.path && parsed.path.startsWith('/') ? parsed.path : defaultPath(url.pathname)
	};

	if (parsed.secure) {
		cookie.secure = true;
	}
	if (parsed.httpOnly) {
		cookie.httpOnly = true;
	}

	const expires = parseExpiration(parsed);
	if (expires !== null) {
		cookie.expires = expires;
	}

	return cookie;
}

/**
 * A leading dot makes the cookie available to subdomains too. For hosts
 * without subdomains (localhost, IP addresses) Chrome stores it as host-only.
 */
function ensureLeadingDot (domain) {
	return domain.startsWith('.') ? domain : `.${domain}`;
}

/**
 * Checks whether hostname is the given domain or a subdomain of it (RFC 6265 5.1.3)
 */
function domainMatches (hostname, domain) {
	const bareDomain = domain.startsWith('.') ? domain.slice(1) : domain;
	return hostname === bareDomain || hostname.endsWith(`.${bareDomain}`);
}

function parseExpiration ({maxAge, expires}) {
	// Max-Age takes precedence over Expires (RFC 6265 5.3)
	if (maxAge !== undefined) {
		return Math.floor(Date.now() / 1000) + maxAge;
	}
	if (expires && !isNaN(expires.getTime())) {
		return Math.floor(expires.getTime() / 1000);
	}
	return null;
}

/**
 * Checks whether a cookie from the browser jar should be sent
 * to the given URL instance (RFC 6265 5.1.3 domain-match / 5.1.4 path-match).
 */
function cookieMatchesUrl (cookie, url) {
	if (cookie.secure && !isSecureContext(url)) {
		return false;
	}

	if (cookie.domain.startsWith('.')) {
		if (!domainMatches(url.hostname, cookie.domain)) {
			return false;
		}
	} else if (url.hostname !== cookie.domain) {
		return false;
	}

	const path = cookie.path;
	return url.pathname === path ||
		(url.pathname.startsWith(path) && (path.endsWith('/') || url.pathname[path.length] === '/'));
}

/**
 * Like Chrome, treats localhost as potentially trustworthy, so Secure cookies
 * are sent to it over http as well - keeping requests and page renders consistent.
 */
function isSecureContext (url) {
	return url.protocol === 'https:' || isLocalhost(url.hostname);
}

function isLocalhost (hostname) {
	if (hostname === 'localhost' || hostname.endsWith('.localhost')) {
		return true;
	}
	return hostname === '127.0.0.1' || hostname === '[::1]';
}

function serializeCookies (cookies) {
	return cookies.map(cookie => `${cookie.name}=${cookie.value}`).join('; ');
}

/**
 * Collects cookies the browser refused to store, so a whole scrape can be
 * explained afterwards instead of silently dropping them. A rejection only
 * affects the rejected cookie itself: every other cookie is still stored and
 * the scrape continues. Each entry contains at least {name, domain, reason}.
 */
function createCookieRejectionTracker () {
	const rejectedCookies = [];

	return {
		record (cookie, reason) {
			const entry = {
				name: cookie.name,
				domain: cookie.domain,
				path: cookie.path,
				reason
			};
			rejectedCookies.push(entry);
			logger.warn(`cookie rejected by browser: ${cookie.name} for ${cookie.domain} (${reason})`);
			return entry;
		},

		getRejectedCookies () {
			// a copy, so callers cannot mutate the tracker's internal state
			return rejectedCookies.map(entry => Object.assign({}, entry));
		},

		getRejectedCookiesSummary () {
			if (rejectedCookies.length === 0) {
				return 'No cookies were rejected by the browser.';
			}
			const lines = rejectedCookies.map(entry =>
				`- ${entry.name} (domain: ${entry.domain}, path: ${entry.path}): ${entry.reason}`);
			return `${rejectedCookies.length} cookie(s) rejected by the browser:\n${lines.join('\n')}`;
		}
	};
}

/**
 * Explains why the browser would refuse to store a cookie, before handing it
 * over. Mirrors the checks Chrome runs on Storage.setCookies
 * (cookie size limit and the __Secure-/__Host- prefix rules). Returns null
 * when nothing locally known would make the browser reject the cookie;
 * a browser-side rejection then falls back to the browser's own error message.
 */
function getLocalRejectionReason (cookie) {
	// Chrome stores cookie values up to 4096 bytes
	if (cookie.value && cookie.value.length > 4096) {
		return `cookie value is ${cookie.value.length} bytes long, exceeding the 4096 byte browser limit`;
	}
	if (cookie.name.startsWith('__Secure-') && !cookie.secure) {
		return '__Secure- prefixed cookies must be set with the Secure attribute';
	}
	if (cookie.name.startsWith('__Host-')) {
		if (!cookie.secure) {
			return '__Host- prefixed cookies must be set with the Secure attribute';
		}
		// an explicit Domain attribute is normalized to a leading dot in parseSetCookieHeader
		if (cookie.domain.startsWith('.')) {
			return '__Host- prefixed cookies must not have a Domain attribute';
		}
		if (cookie.path !== '/') {
			return '__Host- prefixed cookies must have Path=/';
		}
	}
	return null;
}

/**
 * Creates a cookie jar for got's `cookieJar` option backed by the browser's jar,
 * so plain HTTP requests and puppeteer pages share one source of truth.
 * got calls it for every request and every redirect hop.
 * See https://github.com/sindresorhus/got/blob/main/documentation/2-options.md#cookiejar
 *
 * Reading the browser jar is a round trip copying all cookies, so it is cached:
 * while no page is open the jar only changes through setCookie() below.
 * The plugin calls invalidate() once a page render finished.
 */
function createBrowserCookieJar (browser, { cache = true, rejectionTracker = createCookieRejectionTracker() } = {}) {
	let cachedCookies = null;

	function readCookies () {
		if (!cache) {
			return browser.cookies();
		}
		if (!cachedCookies) {
			cachedCookies = browser.cookies().catch(error => {
				cachedCookies = null;
				throw error;
			});
		}
		return cachedCookies;
	}

	return {
		async getCookieString (url) {
			const parsedUrl = new URL(url);
			const cookies = (await readCookies()).filter(cookie => cookieMatchesUrl(cookie, parsedUrl));
			return serializeCookies(cookies);
		},

		async setCookie (setCookieHeader, url) {
			const cookie = parseSetCookieHeader(setCookieHeader, url);
			if (cookie) {
				// an already expired cookie removes the stored one, like browser.deleteCookie() does
				await storeBrowserCookie(browser, cookie, rejectionTracker);
				// the jar changed (or was meant to), so the cached snapshot must not be reused
				cachedCookies = null;
			}
		},

		invalidate () {
			cachedCookies = null;
		},

		getRejectedCookies: () => rejectionTracker.getRejectedCookies(),
		getRejectedCookiesSummary: () => rejectionTracker.getRejectedCookiesSummary()
	};
}

/**
 * Stores a cookie into the browser jar. A cookie the browser rejects
 * (e.g. value over 4096 bytes, __Secure-/__Host- prefix with wrong attributes)
 * is recorded and dropped with a warning instead of failing the scrape,
 * so a single bad cookie neither blocks the remaining cookies nor the whole scrape.
 */
async function storeBrowserCookie (browser, cookie, rejectionTracker = createCookieRejectionTracker()) {
	const localReason = getLocalRejectionReason(cookie);
	if (localReason) {
		rejectionTracker.record(cookie, localReason);
		return;
	}
	try {
		await browser.setCookie(cookie);
	} catch (error) {
		rejectionTracker.record(cookie, error.message);
	}
}

function getCookieHeader (headers) {
	const key = Object.keys(headers).find(isCookieHeaderName);
	return key ? headers[key] : null;
}

function deleteCookieHeader (headers) {
	Object.keys(headers).filter(isCookieHeaderName).forEach(key => delete headers[key]);
}

function isCookieHeaderName (headerName) {
	return headerName.toLowerCase() === 'cookie';
}

/**
 * Default cookie path for a Set-Cookie header without a Path attribute (RFC 6265 5.1.4)
 */
function defaultPath (pathname) {
	const lastSlashIndex = pathname.lastIndexOf('/');
	return lastSlashIndex <= 0 ? '/' : pathname.slice(0, lastSlashIndex);
}

export { parseCookieHeader, parseSetCookieHeader, cookieMatchesUrl, serializeCookies, createBrowserCookieJar, createCookieRejectionTracker, getLocalRejectionReason, storeBrowserCookie, ensureLeadingDot, getCookieHeader, deleteCookieHeader };
