/* eslint-disable dot-notation -- Accessing private properties */
/* eslint-disable max-classes-per-file -- Test doubles for the plugin/controller/browser trio */
import { BrowserPlugin } from '../src/abstract-classes/browser-plugin';
import { BrowserController } from '../src/abstract-classes/browser-controller';
import { LaunchContext } from '../src/launch-context';
import { BrowserPool } from '../src/browser-pool';

const PROXY_A = 'http://user:pass@127.0.0.10:8000';
const PROXY_B = 'http://user:pass@127.0.0.20:9000';
const PROXY_C = 'http://127.0.0.30:7000';

class FakePage {
    async close(): Promise<void> {
        // The real controllers decrement `activePages` on the page `close` event.
    }

    url(): string {
        return 'about:blank';
    }
}

interface FakeBrowserLike {
    newPage(...args: unknown[]): Promise<FakePage>;
    close(): Promise<void>;
}

/**
 * Minimal library stub consumed by `FakePlugin`.
 */
const fakeLibrary = {
    launch: jest.fn(async (): Promise<FakeBrowserLike> => ({
        newPage: jest.fn(async () => new FakePage()),
        close: jest.fn(async () => undefined),
    })),
};

class FakeController extends BrowserController<any, any, FakeBrowserLike, any, FakePage> {
    normalizeProxyOptions(proxyUrl: string | undefined): Record<string, unknown> {
        if (!proxyUrl) return {};
        const url = new URL(proxyUrl);
        return {
            proxy: {
                server: url.origin,
                username: decodeURIComponent(url.username),
                password: decodeURIComponent(url.password),
            },
        };
    }

    protected async _close(): Promise<void> {
        await this.browser.close();
    }

    protected async _kill(): Promise<void> {
        await this.browser.close();
    }

    protected async _newPage(pageOptions?: unknown): Promise<FakePage> {
        if (pageOptions !== undefined && !this.launchContext.useIncognitoPages) {
            throw new Error('A new page can be created with provided context only when using incognito pages.');
        }

        const page = await this.browser.newPage(pageOptions);
        const originalClose = page.close.bind(page);
        page.close = async () => {
            this.activePages--;
            return originalClose();
        };

        return page;
    }

    protected async _setCookies(): Promise<void> {
        // no-op test double
    }

    protected async _getCookies(): Promise<never[]> {
        return [];
    }
}

class FakePlugin extends BrowserPlugin<any, any, FakeBrowserLike, any, FakePage> {
    protected _createController(): BrowserController<any, any, FakeBrowserLike, any, FakePage> {
        return new FakeController(this);
    }

    // eslint-disable-next-line @typescript-eslint/no-empty-function -- Launch options are generated in BrowserPlugin.launch()
    protected async _addProxyToLaunchOptions(): Promise<void> {}

    protected _isChromiumBasedBrowser(): boolean {
        return true;
    }

    protected async _launch(launchContext: LaunchContext<any, any, FakeBrowserLike>): Promise<FakeBrowserLike> {
        // Mimic the real plugins: the browser is launched through the LaunchContext proxy.
        return this.library.launch(launchContext.launchOptions);
    }
}

describe('BrowserPool proxy-aware browser selection', () => {
    let plugin: FakePlugin;
    let browserPool: BrowserPool<any>;

    beforeEach(() => {
        fakeLibrary.launch.mockClear();
        plugin = new FakePlugin(fakeLibrary as never);
        browserPool = new BrowserPool({
            browserPlugins: [plugin as unknown as BrowserPlugin],
            closeInactiveBrowserAfterSecs: 2,
        });
    });

    afterEach(async () => {
        await browserPool?.destroy();
    });

    const getController = (page: FakePage): BrowserController => (
        browserPool.getBrowserControllerByPage(page as never) as unknown as BrowserController
    );

    const proxyOf = (page: FakePage): string | undefined => getController(page).launchContext.proxyUrl;

    const allControllers = (): BrowserController[] => [
        ...(browserPool.activeBrowserControllers as unknown as Set<BrowserController>),
    ];

    test('reuses the same active browser for pages with the same proxy', async () => {
        const firstPage = await browserPool.newPage({ proxyUrl: PROXY_A });
        const secondPage = await browserPool.newPage({ proxyUrl: PROXY_A });

        expect(browserPool.activeBrowserControllers.size).toBe(1);
        expect(fakeLibrary.launch).toHaveBeenCalledTimes(1);
        expect(getController(secondPage)).toBe(getController(firstPage));
        expect(getController(firstPage).launchContext.proxyUrl).toBe(PROXY_A);
    });

    test('isolates pages with different proxies into separate browsers', async () => {
        const pageA = await browserPool.newPage({ proxyUrl: PROXY_A });
        const pageB = await browserPool.newPage({ proxyUrl: PROXY_B });
        const pageC = await browserPool.newPage({ proxyUrl: PROXY_C });

        expect(browserPool.activeBrowserControllers.size).toBe(3);
        expect(fakeLibrary.launch).toHaveBeenCalledTimes(3);

        const controllers = [pageA, pageB, pageC].map(getController);
        expect(new Set(controllers).size).toBe(3);
        expect(proxyOf(pageA)).toBe(PROXY_A);
        expect(proxyOf(pageB)).toBe(PROXY_B);
        expect(proxyOf(pageC)).toBe(PROXY_C);

        // A subsequent request for an existing proxy goes back to its browser.
        const anotherPageA = await browserPool.newPage({ proxyUrl: PROXY_A });
        expect(browserPool.activeBrowserControllers.size).toBe(3);
        expect(fakeLibrary.launch).toHaveBeenCalledTimes(3);
        expect(getController(anotherPageA)).toBe(getController(pageA));
    });

    test('keeps the original reuse behavior when no proxyUrl is passed', async () => {
        const firstPage = await browserPool.newPage();
        const secondPage = await browserPool.newPage();

        expect(browserPool.activeBrowserControllers.size).toBe(1);
        expect(fakeLibrary.launch).toHaveBeenCalledTimes(1);
        expect(getController(secondPage)).toBe(getController(firstPage));
    });

    test('can reuse a proxy-launched browser for requests without a proxyUrl', async () => {
        const proxiedPage = await browserPool.newPage({ proxyUrl: PROXY_A });
        const plainPage = await browserPool.newPage();

        expect(browserPool.activeBrowserControllers.size).toBe(1);
        expect(getController(plainPage)).toBe(getController(proxiedPage));
    });

    test('does not silently reuse a shared-context browser whose proxy differs', async () => {
        const sharedPlugin = new FakePlugin(fakeLibrary as never, { useIncognitoPages: false });
        const pool = new BrowserPool({
            browserPlugins: [sharedPlugin as unknown as BrowserPlugin],
            closeInactiveBrowserAfterSecs: 2,
        }) as BrowserPool<any>;
        const getPoolController = (page: FakePage): BrowserController => (
            pool.getBrowserControllerByPage(page as never) as unknown as BrowserController
        );

        try {
            const pageA = await pool.newPage({ proxyUrl: PROXY_A });
            const pageB = await pool.newPage({ proxyUrl: PROXY_B });

            expect(pool.activeBrowserControllers.size).toBe(2);
            expect(getPoolController(pageB)).not.toBe(getPoolController(pageA));
            expect(getPoolController(pageA).launchContext.proxyUrl).toBe(PROXY_A);
            expect(getPoolController(pageB).launchContext.proxyUrl).toBe(PROXY_B);
        } finally {
            await pool.destroy();
        }
    });

    test('records the proxy on the new browser LaunchContext at launch', async () => {
        const launchContexts: LaunchContext[] = [];
        browserPool.preLaunchHooks = [
            (_pageId, launchContext) => {
                launchContexts.push(launchContext);
            },
        ];

        await browserPool.newPage({ proxyUrl: PROXY_A });
        await browserPool.newPage({ proxyUrl: PROXY_B });

        expect(launchContexts).toHaveLength(2);
        expect(launchContexts[0].proxyUrl).toBe(PROXY_A);
        expect(launchContexts[1].proxyUrl).toBe(PROXY_B);
    });

    test('treats equivalent proxy URLs (path/trailing slash differences) as the same proxy', async () => {
        const pageA = await browserPool.newPage({ proxyUrl: `${PROXY_A}/some/routing/path?session=1` });
        const pageB = await browserPool.newPage({ proxyUrl: `${PROXY_A}/` });

        expect(browserPool.activeBrowserControllers.size).toBe(1);
        expect(fakeLibrary.launch).toHaveBeenCalledTimes(1);
        expect(getController(pageB)).toBe(getController(pageA));
        expect(proxyOf(pageA)).toBe(PROXY_A);
    });

    test('launches a new browser when every matching-proxy browser is full', async () => {
        browserPool.maxOpenPagesPerBrowser = 2;

        await browserPool.newPage({ proxyUrl: PROXY_A });
        await browserPool.newPage({ proxyUrl: PROXY_A });
        expect(browserPool.activeBrowserControllers.size).toBe(1);

        // The PROXY_A browser is full, so another PROXY_A browser is launched,
        // while PROXY_B still gets its own isolated browser.
        await browserPool.newPage({ proxyUrl: PROXY_A });
        await browserPool.newPage({ proxyUrl: PROXY_B });

        expect(browserPool.activeBrowserControllers.size).toBe(3);
        expect(fakeLibrary.launch).toHaveBeenCalledTimes(3);

        const proxies = allControllers().map((controller) => controller.launchContext.proxyUrl);
        expect(proxies.filter((proxy) => proxy === PROXY_A)).toHaveLength(2);
        expect(proxies.filter((proxy) => proxy === PROXY_B)).toHaveLength(1);
    });

    test('concurrent requests for the same proxy open a single browser', async () => {
        const pages = await Promise.all([
            browserPool.newPage({ proxyUrl: PROXY_A }),
            browserPool.newPage({ proxyUrl: PROXY_A }),
            browserPool.newPage({ proxyUrl: PROXY_A }),
        ]);

        expect(browserPool.activeBrowserControllers.size).toBe(1);
        expect(fakeLibrary.launch).toHaveBeenCalledTimes(1);

        const controllers = pages.map(getController);
        expect(new Set(controllers).size).toBe(1);
        controllers.forEach((controller) => expect(controller.launchContext.proxyUrl).toBe(PROXY_A));
    });

    test('concurrent requests for different proxies never share a browser', async () => {
        browserPool.maxOpenPagesPerBrowser = 1;

        const requests = [
            PROXY_A, PROXY_B, PROXY_A, PROXY_B, PROXY_C, PROXY_A, PROXY_B, PROXY_C,
        ];
        const pages = await Promise.all(requests.map((proxyUrl) => browserPool.newPage({ proxyUrl })));

        // Capacity is one page per browser, so 8 requests open 8 browsers...
        expect(browserPool.activeBrowserControllers.size).toBe(requests.length);
        expect(fakeLibrary.launch).toHaveBeenCalledTimes(requests.length);

        pages.forEach((page, index) => {
            expect(proxyOf(page)).toBe(requests[index]);
        });

        // ...but every browser is owned by exactly one proxy, i.e. no browser
        // ever hosts pages that requested different proxies.
        const proxiesPerController = allControllers().map((controller) => controller.launchContext.proxyUrl);
        expect(new Set(proxiesPerController).size).toBe(3);
        expect(proxiesPerController.filter((proxy) => proxy === PROXY_A)).toHaveLength(3);
        expect(proxiesPerController.filter((proxy) => proxy === PROXY_B)).toHaveLength(3);
        expect(proxiesPerController.filter((proxy) => proxy === PROXY_C)).toHaveLength(2);
    });

    test('concurrent requests without a proxy keep reusing one browser', async () => {
        const pages = await Promise.all([
            browserPool.newPage(),
            browserPool.newPage(),
            browserPool.newPage(),
            browserPool.newPage(),
        ]);

        expect(browserPool.activeBrowserControllers.size).toBe(1);
        expect(fakeLibrary.launch).toHaveBeenCalledTimes(1);

        const controllers = pages.map(getController);
        expect(new Set(controllers).size).toBe(1);
    });

    test('concurrent mixed proxy and proxy-less requests attribute every page correctly', async () => {
        const pages = await Promise.all([
            browserPool.newPage({ proxyUrl: PROXY_A }),
            browserPool.newPage({ proxyUrl: PROXY_B }),
            browserPool.newPage(),
            browserPool.newPage({ proxyUrl: PROXY_A }),
            browserPool.newPage({ proxyUrl: PROXY_B }),
            browserPool.newPage(),
        ]);

        // One browser per proxy (2) and the proxy-less requests reuse an existing one.
        expect(browserPool.activeBrowserControllers.size).toBe(2);
        expect(fakeLibrary.launch).toHaveBeenCalledTimes(2);

        const [pageA1, pageB1, plain1, pageA2, pageB2, plain2] = pages;
        expect(getController(pageA2)).toBe(getController(pageA1));
        expect(getController(pageB2)).toBe(getController(pageB1));
        expect(proxyOf(pageA1)).toBe(PROXY_A);
        expect(proxyOf(pageB1)).toBe(PROXY_B);

        // Proxy-less pages may land in any browser, but a third browser is never launched.
        const activeSet = browserPool.activeBrowserControllers as unknown as Set<BrowserController>;
        expect([getController(plain1), getController(plain2)].every((controller) => activeSet.has(controller))).toBe(true);
    });

    test('uses the plugin-level proxy when no per-page proxyUrl is provided', async () => {
        const pluginWithProxy = new FakePlugin(fakeLibrary as never, { proxyUrl: PROXY_A });
        const pool = new BrowserPool({
            browserPlugins: [pluginWithProxy as unknown as BrowserPlugin],
            closeInactiveBrowserAfterSecs: 2,
        }) as BrowserPool<any>;
        const getPoolController = (page: FakePage): BrowserController => (
            pool.getBrowserControllerByPage(page as never) as unknown as BrowserController
        );

        try {
            // The plugin stores the proxy in its canonical URL form (trailing slash).
            const page = await pool.newPage();
            expect(getPoolController(page).launchContext.proxyUrl).toBe(`${PROXY_A}/`);

            // An equivalent per-page proxy URL (normalized, no trailing slash) must
            // still match the plugin-proxy browser thanks to comparison normalization.
            const equivalentPage = await pool.newPage({ proxyUrl: `${PROXY_A}/` });
            expect(pool.activeBrowserControllers.size).toBe(1);
            expect(getPoolController(equivalentPage)).toBe(getPoolController(page));

            // An explicit different proxy must not reuse the plugin-proxy browser.
            const otherPage = await pool.newPage({ proxyUrl: PROXY_B });
            expect(pool.activeBrowserControllers.size).toBe(2);
            expect(getPoolController(otherPage).launchContext.proxyUrl).toBe(PROXY_B);
        } finally {
            await pool.destroy();
        }
    });
});
