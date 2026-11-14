import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

// Resolved by test/helpers/apify-mock-loader.mjs to the same in-memory module as the `apify` import
import { mockState } from 'apify-mock-state';
import { detailRoute } from '../src/routes/detailRoute.js';
import {
    buildFullGraphqlCache,
    cacheKey,
    PRODUCT_URL,
} from './fixtures/graphql-product-cache.js';
import { createRecordingLogger } from './helpers/recording-logger.js';
import { PRODUCT_ATTRIBUTES_GRAPHQL_ID } from '../src/constants.js';

type FakeScript = { text: string };

/** Minimal Cheerio-like root supporting only what parseGraphqlCache and the route use */
const createFakeCheerio = (scripts: FakeScript[]) => {
    const root = (selectorOrElement: string | FakeScript) => {
        // Cheerio calls $(element) as well as $(selector); the cache parser then reads .text()
        if (typeof selectorOrElement !== 'string') {
            return { text: () => selectorOrElement.text };
        }

        if (selectorOrElement === 'head title') {
            return { text: () => 'Test product title' };
        }

        return {
            each: (callback: (_index: number, script: FakeScript) => void) => {
                scripts.forEach((script, index) => callback(index, script));
            },
        };
    };

    return root;
};

const createContext = (cache: Record<string, unknown> | null) => {
    const scripts: FakeScript[] = cache === null
        ? [{ text: 'window.__hydrationDataConsume({not valid json);' }]
        : [{ text: `runtime['hydratePartial'](${JSON.stringify({ graphqlCache: cache })});` }];

    return {
        request: { url: PRODUCT_URL },
        $: createFakeCheerio(scripts),
        log: createRecordingLogger(),
        crawler: {
            useState: async () => ({
                get remainingItems() {
                    return mockState.remainingItems;
                },
                set remainingItems(value: number) {
                    mockState.remainingItems = value;
                },
            }),
        },
    };
};

beforeEach(() => {
    mockState.pushedData.length = 0;
    mockState.remainingItems = 10;
});

describe('detailRoute quota and dataset behavior', () => {
    it('pushes exactly one record and decrements the quota once for a valid product', async () => {
        const context = createContext(buildFullGraphqlCache());

        await detailRoute(context as never);

        assert.equal(mockState.pushedData.length, 1);
        assert.equal(mockState.remainingItems, 9);
        assert.equal((mockState.pushedData[0] as { url: string }).url, PRODUCT_URL);
        assert.equal((mockState.pushedData[0] as { sku: string }).sku, 'AN621C1XR-G11');
    });

    it('pushes one record and decrements the quota once even when blocks are partially missing', async () => {
        const fullCache = buildFullGraphqlCache();
        delete fullCache[cacheKey(PRODUCT_ATTRIBUTES_GRAPHQL_ID)];

        const context = createContext(fullCache);

        await detailRoute(context as never);

        assert.equal(mockState.pushedData.length, 1);
        assert.equal(mockState.remainingItems, 9);
        assert.equal(
            (mockState.pushedData[0] as { attributeCategories: unknown }).attributeCategories,
            null,
        );
    });

    it('does not push data and does not consume quota when name and SKU are both unobtainable', async () => {
        const context = createContext({});

        await detailRoute(context as never);

        assert.equal(mockState.pushedData.length, 0);
        assert.equal(mockState.remainingItems, 10);
    });

    it('does not push data and does not consume quota when the embedded cache is unreadable', async () => {
        const context = createContext(null);

        await detailRoute(context as never);

        assert.equal(mockState.pushedData.length, 0);
        assert.equal(mockState.remainingItems, 10);
    });

    it('does not push data when the remaining quota is already zero', async () => {
        mockState.remainingItems = 0;
        const context = createContext(buildFullGraphqlCache());

        await detailRoute(context as never);

        assert.equal(mockState.pushedData.length, 0);
        assert.equal(mockState.remainingItems, 0);
    });
});
