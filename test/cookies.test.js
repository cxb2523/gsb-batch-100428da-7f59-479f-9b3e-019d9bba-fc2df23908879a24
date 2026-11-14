import { expect } from 'chai';
import { parseCookieHeader, parseSetCookieHeader, cookieMatchesUrl, serializeCookies, createBrowserCookieJar, createCookieRejectionTracker, getLocalRejectionReason, storeBrowserCookie, ensureLeadingDot, getCookieHeader, deleteCookieHeader } from '../lib/cookies.js';

describe('Cookies', () => {
	describe('parseCookieHeader', () => {
		it('should parse name=value pairs', () => {
			expect(parseCookieHeader('a=1; b=2')).to.eql([
				{ name: 'a', value: '1' },
				{ name: 'b', value: '2' }
			]);
		});

		it('should keep = characters inside values', () => {
			expect(parseCookieHeader('token=abc=def')).to.eql([{ name: 'token', value: 'abc=def' }]);
		});

		it('should skip malformed pairs', () => {
			expect(parseCookieHeader('malformed; a=1; =nameless')).to.eql([{ name: 'a', value: '1' }]);
		});
	});

	describe('parseSetCookieHeader', () => {
		const url = 'https://example.com/shop/cart';

		it('should default domain and path from the request url', () => {
			expect(parseSetCookieHeader('session=xyz', url)).to.eql({
				name: 'session',
				value: 'xyz',
				domain: 'example.com',
				path: '/shop'
			});
		});

		it('should parse attributes', () => {
			const cookie = parseSetCookieHeader('session=xyz; Domain=example.com; Path=/; Secure; HttpOnly', url);
			expect(cookie).to.include({
				name: 'session',
				value: 'xyz',
				domain: '.example.com',
				path: '/',
				secure: true,
				httpOnly: true
			});
		});

		it('should prefer Max-Age over Expires', () => {
			const cookie = parseSetCookieHeader('a=1; Expires=Wed, 01 Jan 2020 00:00:00 GMT; Max-Age=60', url);
			expect(cookie.expires).to.be.greaterThan(Date.now() / 1000);
		});

		it('should parse Expires into a unix timestamp', () => {
			const cookie = parseSetCookieHeader('a=1; Expires=Wed, 01 Jan 2020 00:00:00 GMT', url);
			expect(cookie.expires).to.eql(Date.parse('Wed, 01 Jan 2020 00:00:00 GMT') / 1000);
		});

		it('should return null for malformed headers', () => {
			expect(parseSetCookieHeader('malformed', url)).to.eql(null);
			expect(parseSetCookieHeader('=nameless', url)).to.eql(null);
		});

		it('should accept a Domain covering the request host', () => {
			expect(parseSetCookieHeader('a=1; Domain=example.com', 'https://shop.example.com/').domain).to.eql('.example.com');
			expect(parseSetCookieHeader('a=1; Domain=.example.com', 'https://example.com/').domain).to.eql('.example.com');
		});

		it('should reject a Domain not covering the request host', () => {
			expect(parseSetCookieHeader('session=attacker; Domain=target.com', 'https://ads.example.net/pixel.gif')).to.eql(null);
			expect(parseSetCookieHeader('a=1; Domain=shop.example.com', 'https://example.com/')).to.eql(null);
			expect(parseSetCookieHeader('a=1; Domain=example.com', 'https://badexample.com/')).to.eql(null);
		});
	});

	describe('cookieMatchesUrl', () => {
		const matches = (cookie, url) => cookieMatchesUrl(cookie, new URL(url));

		it('should match host-only cookies only on the exact host', () => {
			const cookie = { name: 'a', value: '1', domain: 'example.com', path: '/' };
			expect(matches(cookie, 'http://example.com/page')).to.eql(true);
			expect(matches(cookie, 'http://sub.example.com/page')).to.eql(false);
			expect(matches(cookie, 'http://other.com/page')).to.eql(false);
		});

		it('should match domain cookies on subdomains', () => {
			const cookie = { name: 'a', value: '1', domain: '.example.com', path: '/' };
			expect(matches(cookie, 'http://example.com/')).to.eql(true);
			expect(matches(cookie, 'http://sub.example.com/')).to.eql(true);
			expect(matches(cookie, 'http://badexample.com/')).to.eql(false);
		});

		it('should match paths on segment boundaries', () => {
			const cookie = { name: 'a', value: '1', domain: 'example.com', path: '/shop' };
			expect(matches(cookie, 'http://example.com/shop')).to.eql(true);
			expect(matches(cookie, 'http://example.com/shop/cart')).to.eql(true);
			expect(matches(cookie, 'http://example.com/shopping')).to.eql(false);
			expect(matches(cookie, 'http://example.com/')).to.eql(false);
		});

		it('should not send secure cookies over http', () => {
			const cookie = { name: 'a', value: '1', domain: 'example.com', path: '/', secure: true };
			expect(matches(cookie, 'http://example.com/')).to.eql(false);
			expect(matches(cookie, 'https://example.com/')).to.eql(true);
		});

		it('should send secure cookies to localhost over http, like Chrome does', () => {
			const secure = domain => ({ name: 'a', value: '1', domain, path: '/', secure: true });
			expect(matches(secure('localhost'), 'http://localhost:3000/')).to.eql(true);
			expect(matches(secure('.localhost'), 'http://app.localhost/')).to.eql(true);
			expect(matches(secure('127.0.0.1'), 'http://127.0.0.1/')).to.eql(true);
			expect(matches(secure('[::1]'), 'http://[::1]/')).to.eql(true);
		});
	});

	describe('ensureLeadingDot', () => {
		it('should add a leading dot once', () => {
			expect(ensureLeadingDot('example.com')).to.eql('.example.com');
			expect(ensureLeadingDot('.example.com')).to.eql('.example.com');
			expect(ensureLeadingDot('localhost')).to.eql('.localhost');
		});
	});

	describe('getCookieHeader', () => {
		it('should find the Cookie header case-insensitively', () => {
			expect(getCookieHeader({ Cookie: 'a=1' })).to.eql('a=1');
			expect(getCookieHeader({ cookie: 'a=1' })).to.eql('a=1');
			expect(getCookieHeader({ Accept: 'text/html' })).to.eql(null);
		});
	});

	describe('deleteCookieHeader', () => {
		it('should delete the Cookie header case-insensitively', () => {
			const headers = { Cookie: 'a=1', cookie: 'b=2', Accept: 'text/html' };
			deleteCookieHeader(headers);
			expect(headers).to.eql({ Accept: 'text/html' });
		});
	});

	describe('serializeCookies', () => {
		it('should serialize cookies into a Cookie header value', () => {
			const cookies = [{ name: 'a', value: '1' }, { name: 'b', value: '2' }];
			expect(serializeCookies(cookies)).to.eql('a=1; b=2');
		});
	});

	describe('createBrowserCookieJar', () => {
		const fakeBrowser = (cookies) => ({
			cookies: async () => cookies,
			setCookie: async (...newCookies) => cookies.push(...newCookies)
		});

		it('should serialize only the cookies which match the url', async () => {
			const jar = createBrowserCookieJar(fakeBrowser([
				{ name: 'a', value: '1', domain: 'example.com', path: '/' },
				{ name: 'b', value: '2', domain: 'other.com', path: '/' },
				{ name: 'c', value: '3', domain: 'example.com', path: '/', secure: true }
			]));
			expect(await jar.getCookieString('http://example.com/page')).to.eql('a=1');
			expect(await jar.getCookieString('https://example.com/page')).to.eql('a=1; c=3');
			expect(await jar.getCookieString('http://unknown.com/')).to.eql('');
		});

		it('should store Set-Cookie headers into the browser', async () => {
			const stored = [];
			const jar = createBrowserCookieJar(fakeBrowser(stored));
			await jar.setCookie('session=xyz; Path=/', 'https://example.com/page');
			expect(stored).to.eql([{ name: 'session', value: 'xyz', domain: 'example.com', path: '/' }]);
		});

		it('should ignore malformed Set-Cookie headers', async () => {
			const stored = [];
			const jar = createBrowserCookieJar(fakeBrowser(stored));
			await jar.setCookie('malformed', 'https://example.com/');
			expect(stored).to.eql([]);
		});

		it('should read the browser jar once until a cookie is stored or the cache is invalidated', async () => {
			let reads = 0;
			const cookies = [{ name: 'a', value: '1', domain: 'example.com', path: '/' }];
			const jar = createBrowserCookieJar({
				cookies: async () => { reads++; return cookies; },
				setCookie: async cookie => cookies.push(cookie)
			});

			await Promise.all([jar.getCookieString('http://example.com/'), jar.getCookieString('http://example.com/a')]);
			await jar.getCookieString('http://example.com/b');
			expect(reads).to.eql(1);

			await jar.setCookie('b=2; Path=/', 'http://example.com/');
			expect(await jar.getCookieString('http://example.com/')).to.eql('a=1; b=2');
			expect(reads).to.eql(2);

			jar.invalidate();
			await jar.getCookieString('http://example.com/');
			expect(reads).to.eql(3);
		});

		it('should read the browser jar for every request with cache disabled', async () => {
			let reads = 0;
			const jar = createBrowserCookieJar({ cookies: async () => { reads++; return []; } }, { cache: false });
			await jar.getCookieString('http://example.com/');
			await jar.getCookieString('http://example.com/');
			expect(reads).to.eql(2);
		});

		it('should not cache a failed read of the browser jar', async () => {
			let fail = true;
			const jar = createBrowserCookieJar({
				cookies: async () => {
					if (fail) {
						throw new Error('browser closed');
					}
					return [];
				}
			});
			await jar.getCookieString('http://example.com/').then(() => { throw new Error('should reject'); }, () => {});
			fail = false;
			expect(await jar.getCookieString('http://example.com/')).to.eql('');
		});

		it('should record cookies rejected by the browser without failing', async () => {
			const rejectingBrowser = {
				cookies: async () => [],
				setCookie: async () => { throw new Error('Protocol error (Storage.setCookies): Invalid cookie fields'); }
			};
			const jar = createBrowserCookieJar(rejectingBrowser);
			await jar.setCookie('a=1; Path=/', 'http://example.com/');

			const rejected = jar.getRejectedCookies();
			expect(rejected).to.have.lengthOf(1);
			expect(rejected[0]).to.include({ name: 'a', domain: 'example.com' });
			expect(rejected[0].reason).to.contain('Invalid cookie fields');
			// the scrape continues: the jar still answers cookie requests afterwards
			expect(await jar.getCookieString('http://example.com/')).to.eql('');
		});

		it('should keep storing other cookies when one is rejected', async () => {
			const stored = [];
			const browser = {
				cookies: async () => stored,
				setCookie: async cookie => {
					if (cookie.name === 'bad') {
						throw new Error('Invalid cookie fields');
					}
					stored.push(cookie);
				}
			};
			const jar = createBrowserCookieJar(browser);

			await jar.setCookie('bad=1; Path=/', 'https://example.com/');
			await jar.setCookie('good=2; Path=/', 'https://example.com/');

			expect(jar.getRejectedCookies().map(cookie => cookie.name)).to.eql(['bad']);
			expect(stored.map(cookie => cookie.name)).to.eql(['good']);
			expect(await jar.getCookieString('https://example.com/')).to.eql('good=2');
		});

		it('should invalidate the cached jar snapshot even when the cookie was rejected', async () => {
			let reads = 0;
			const jar = createBrowserCookieJar({
				cookies: async () => { reads++; return []; },
				setCookie: async () => { throw new Error('Invalid cookie fields'); }
			});
			await jar.getCookieString('http://example.com/');
			await jar.setCookie('bad=1; Path=/', 'http://example.com/');
			await jar.getCookieString('http://example.com/');
			expect(reads).to.eql(2);
		});

		it('should expose an empty rejection list and an explicit empty summary', () => {
			const jar = createBrowserCookieJar({
				cookies: async () => [],
				setCookie: async () => {}
			});
			expect(jar.getRejectedCookies()).to.eql([]);
			expect(jar.getRejectedCookiesSummary()).to.eql('No cookies were rejected by the browser.');
		});

		it('should build a readable summary of every rejected cookie', async () => {
			const jar = createBrowserCookieJar({
				cookies: async () => [],
				setCookie: async () => { throw new Error('Invalid cookie fields'); }
			});
			await jar.setCookie('a=1; Path=/', 'http://example.com/');

			const summary = jar.getRejectedCookiesSummary();
			expect(summary).to.contain('1 cookie(s) rejected');
			expect(summary).to.contain('a (domain: example.com');
			expect(summary).to.contain('Invalid cookie fields');
		});

		it('should share the rejection tracker passed in the options', async () => {
			const tracker = createCookieRejectionTracker();
			const jar = createBrowserCookieJar({
				cookies: async () => [],
				setCookie: async () => { throw new Error('boom'); }
			}, { rejectionTracker: tracker });
			await jar.setCookie('a=1; Path=/', 'http://example.com/');
			expect(tracker.getRejectedCookies()).to.have.lengthOf(1);
			expect(jar.getRejectedCookies()).to.eql(tracker.getRejectedCookies());
		});
	});

	describe('createCookieRejectionTracker', () => {
		it('should start empty and say so explicitly', () => {
			const tracker = createCookieRejectionTracker();
			expect(tracker.getRejectedCookies()).to.eql([]);
			expect(tracker.getRejectedCookiesSummary()).to.eql('No cookies were rejected by the browser.');
		});

		it('should record name, domain, path and reason for every rejected cookie', () => {
			const tracker = createCookieRejectionTracker();
			tracker.record({ name: 'a', domain: 'example.com', path: '/' }, 'too large');
			tracker.record({ name: 'b', domain: '.example.com', path: '/shop' }, 'bad prefix');

			expect(tracker.getRejectedCookies()).to.eql([
				{ name: 'a', domain: 'example.com', path: '/', reason: 'too large' },
				{ name: 'b', domain: '.example.com', path: '/shop', reason: 'bad prefix' }
			]);
			expect(tracker.getRejectedCookiesSummary()).to.contain('2 cookie(s) rejected');
		});

		it('should return copies so the internal list cannot be mutated', () => {
			const tracker = createCookieRejectionTracker();
			tracker.record({ name: 'a', domain: 'example.com', path: '/' }, 'reason');
			const rejected = tracker.getRejectedCookies();
			rejected[0].name = 'tampered';
			rejected.pop();
			expect(tracker.getRejectedCookies()[0].name).to.eql('a');
		});
	});

	describe('getLocalRejectionReason', () => {
		it('should reject values exceeding the browser size limit', () => {
			const reason = getLocalRejectionReason({ name: 'big', value: 'x'.repeat(4097), domain: 'example.com', path: '/' });
			expect(reason).to.contain('4096');
			expect(getLocalRejectionReason({ name: 'ok', value: 'x'.repeat(4096), domain: 'example.com', path: '/' })).to.eql(null);
		});

		it('should reject __Secure- prefixed cookies without the Secure attribute', () => {
			expect(getLocalRejectionReason({ name: '__Secure-a', value: '1', domain: 'example.com', path: '/' })).to.contain('__Secure-');
			expect(getLocalRejectionReason({ name: '__Secure-a', value: '1', domain: 'example.com', path: '/', secure: true })).to.eql(null);
		});

		it('should reject __Host- prefixed cookies with wrong attributes', () => {
			expect(getLocalRejectionReason({ name: '__Host-a', value: '1', domain: 'example.com', path: '/' })).to.contain('Secure');
			expect(getLocalRejectionReason({ name: '__Host-a', value: '1', domain: '.example.com', path: '/', secure: true })).to.contain('Domain');
			expect(getLocalRejectionReason({ name: '__Host-a', value: '1', domain: 'example.com', path: '/shop', secure: true })).to.contain('Path=/');
			expect(getLocalRejectionReason({ name: '__Host-a', value: '1', domain: 'example.com', path: '/', secure: true })).to.eql(null);
		});

		it('should accept ordinary cookies', () => {
			expect(getLocalRejectionReason({ name: 'session', value: 'abc', domain: 'example.com', path: '/' })).to.eql(null);
		});
	});

	describe('storeBrowserCookie', () => {
		it('should store accepted cookies and leave the rejection tracker empty', async () => {
			const stored = [];
			const tracker = createCookieRejectionTracker();
			await storeBrowserCookie({ setCookie: async cookie => stored.push(cookie) }, { name: 'a', value: '1', domain: 'example.com', path: '/' }, tracker);
			expect(stored).to.have.lengthOf(1);
			expect(tracker.getRejectedCookies()).to.eql([]);
		});

		it('should record locally known rejections without calling the browser', async () => {
			let called = false;
			const tracker = createCookieRejectionTracker();
			await storeBrowserCookie(
				{ setCookie: async () => { called = true; } },
				{ name: '__Secure-a', value: '1', domain: 'example.com', path: '/' },
				tracker
			);
			expect(called).to.eql(false);
			expect(tracker.getRejectedCookies()[0]).to.include({ name: '__Secure-a', domain: 'example.com' });
			expect(tracker.getRejectedCookies()[0].reason).to.contain('Secure');
		});

		it('should record the browser error when it rejects an otherwise valid cookie', async () => {
			const tracker = createCookieRejectionTracker();
			await storeBrowserCookie(
				{ setCookie: async () => { throw new Error('Protocol error: boom'); } },
				{ name: 'a', value: '1', domain: 'example.com', path: '/' },
				tracker
			);
			expect(tracker.getRejectedCookies()[0].reason).to.eql('Protocol error: boom');
		});
	});
});
