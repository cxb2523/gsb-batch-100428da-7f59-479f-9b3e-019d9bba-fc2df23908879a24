import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
    GENERAL_PRODUCT_INFO_GRAPHQL_ID,
    PRICE_CURRENCY_GRAPHQL_ID,
    PRODUCT_ATTRIBUTES_GRAPHQL_ID,
    PRODUCT_IMAGES_GRAPHQL_ID,
    COLOR_PRICE_CATEGORY_GRAPHQL_ID,
    PRODUCT_IMAGES_BLOCK_NAME,
    PRODUCT_ATTRIBUTES_BLOCK_NAME,
    GENERAL_PRODUCT_INFO_BLOCK_NAME,
    COLOR_PRICE_CATEGORY_BLOCK_NAME,
    PRICE_CURRENCY_BLOCK_NAME,
} from '../src/constants.js';
import { parseProductDetail, ProductDetailRecord } from '../src/routes/detail-parser.js';
import { createRecordingLogger } from './helpers/recording-logger.js';
import {
    buildFullGraphqlCache,
    buildMalformedBlock,
    cacheKey,
    PRODUCT_URL,
} from './fixtures/graphql-product-cache.js';

const omitBlock = (
    cache: Record<string, unknown>,
    graphqlId: string,
): Record<string, unknown> => {
    const copy = { ...cache };
    delete copy[cacheKey(graphqlId)];
    return copy;
};

const expectedFullDetail = (): ProductDetailRecord => ({
    url: PRODUCT_URL,
    name: 'Jersey dress - coral option',
    sku: 'AN621C1XR-G11',
    brand: {
        name: 'Anna Field by Zalando',
        uri: 'https://www.zalando.co.uk/anna-field/',
    },
    flags: [
        { formatted: 'Top rated', kind: 'TOP_RATED', info: null },
        { formatted: '-9%', kind: 'DEAL', info: null },
    ],
    comingSoon: false,
    thumbnail: 'https://img01.ztat.net/article/thumb/1586540b69234154b9c13f0a341eb349.jpg?imwidth=156',
    images: [
        'https://img01.ztat.net/article/full/1586540b69234154b9c13f0a341eb349.jpg',
        'https://img01.ztat.net/article/full/5e3f19c0d69b4cc68838c10f956d67ee.jpg',
    ],
    videos: [
        'https://video01.ztat.net/b53eb71b-3425-4b38-99612185cc6bbae9/cmaf/e02ec503990e495094908f5329e31f6c.m3u8',
    ],
    category: 'Jersey dress',
    color: {
        name: 'coral option',
        label: 'coral',
    },
    formattedPrice: {
        original: '£33.99',
        current: '£31.00',
        promotional: '£31.00',
    },
    priceCurrency: 'GBP',
    price: {
        original: 33.99,
        current: 31,
        promotional: 31,
    },
    sizes: [
        { size: 'XS', sku: 'AN621C1XR-G1100XS000', stockStatus: 'OUT_OF_STOCK' },
        { size: 'S', sku: 'AN621C1XR-G11000S000', stockStatus: 'MANY' },
    ],
    available: true,
    sizeAdvice: 'SMALLER_SIZE',
    navigationTargetGroup: 'WOMEN',
    condition: null,
    attributeCategories: [
        {
            categoryId: 'material_care',
            categoryName: 'Material & care',
            attributes: [
                { key: 'Outer fabric material', value: '98% polyester, 2% elastane' },
                { key: 'Fabric', value: 'Jersey' },
            ],
        },
    ],
});

describe('parseProductDetail - complete GraphQL cache', () => {
    it('parses every block and returns the full dataset record without skipped blocks', () => {
        const logger = createRecordingLogger();
        const result = parseProductDetail(buildFullGraphqlCache(), PRODUCT_URL, logger);

        assert.equal(result.skippedBlocks.length, 0);
        assert.deepEqual(result.detail, expectedFullDetail());
        assert.equal(logger.entries.filter((entry) => entry.level === 'warning').length, 0);
    });
});

describe('parseProductDetail - partially missing GraphQL blocks', () => {
    it('nulls only the fields of the missing block and keeps every other block', () => {
        const logger = createRecordingLogger();
        const cacheWithoutAttributes = omitBlock(buildFullGraphqlCache(), PRODUCT_ATTRIBUTES_GRAPHQL_ID);

        const result = parseProductDetail(cacheWithoutAttributes as never, PRODUCT_URL, logger);

        assert.ok(result.detail, 'product must still be written');
        assert.equal(result.detail.condition, null);
        assert.equal(result.detail.attributeCategories, null);
        // Fields owned by the other blocks stay intact
        assert.deepEqual(result.detail.name, 'Jersey dress - coral option');
        assert.deepEqual(result.detail.images, expectedFullDetail().images);
        assert.deepEqual(result.detail.price, expectedFullDetail().price);
        assert.deepEqual(result.detail.color, expectedFullDetail().color);
        assert.equal(result.detail.priceCurrency, 'GBP');

        assert.equal(result.skippedBlocks.length, 1);
        assert.equal(result.skippedBlocks[0].name, PRODUCT_ATTRIBUTES_BLOCK_NAME);
        assert.equal(result.skippedBlocks[0].url, PRODUCT_URL);
        assert.ok(result.skippedBlocks[0].reason.length > 0, 'a missing block must explain why it was skipped');
        const warning = logger.entries.find((entry) => entry.level === 'warning');
        assert.ok(warning?.message.includes(PRODUCT_ATTRIBUTES_BLOCK_NAME));
        assert.deepEqual(warning?.data, {
            block: PRODUCT_ATTRIBUTES_BLOCK_NAME,
            url: PRODUCT_URL,
        });
    });

    it('nulls every field owned by several simultaneously missing blocks', () => {
        const logger = createRecordingLogger();
        let cache = omitBlock(buildFullGraphqlCache(), GENERAL_PRODUCT_INFO_GRAPHQL_ID);
        cache = omitBlock(cache, COLOR_PRICE_CATEGORY_GRAPHQL_ID);
        cache = omitBlock(cache, PRICE_CURRENCY_GRAPHQL_ID);
        cache = omitBlock(cache, PRODUCT_ATTRIBUTES_GRAPHQL_ID);

        const result = parseProductDetail(cache as never, PRODUCT_URL, logger);

        assert.ok(result.detail);
        assert.deepEqual(result.detail, {
            ...expectedFullDetail(),
            category: null,
            color: null,
            formattedPrice: null,
            priceCurrency: null,
            price: null,
            sizes: null,
            available: null,
            sizeAdvice: null,
            navigationTargetGroup: null,
            condition: null,
            attributeCategories: null,
        });
        assert.equal(result.skippedBlocks.length, 4);
        assert.deepEqual(
            result.skippedBlocks.map((block) => block.name).sort(),
            [
                GENERAL_PRODUCT_INFO_BLOCK_NAME,
                COLOR_PRICE_CATEGORY_BLOCK_NAME,
                PRICE_CURRENCY_BLOCK_NAME,
                PRODUCT_ATTRIBUTES_BLOCK_NAME,
            ].sort(),
        );
        for (const block of result.skippedBlocks) {
            assert.equal(block.url, PRODUCT_URL);
        }
    });

    it('tolerates a malformed block payload and still saves the remaining fields', () => {
        const logger = createRecordingLogger();
        const cache = {
            ...omitBlock(buildFullGraphqlCache(), COLOR_PRICE_CATEGORY_GRAPHQL_ID),
            [cacheKey(COLOR_PRICE_CATEGORY_GRAPHQL_ID)]: buildMalformedBlock(),
        };

        const result = parseProductDetail(cache as never, PRODUCT_URL, logger);

        assert.ok(result.detail);
        assert.equal(result.detail.category, null);
        assert.equal(result.detail.color, null);
        assert.equal(result.detail.formattedPrice, null);
        assert.deepEqual(result.detail.sizes, expectedFullDetail().sizes);
        assert.equal(result.skippedBlocks.length, 1);
        assert.equal(result.skippedBlocks[0].name, COLOR_PRICE_CATEGORY_BLOCK_NAME);
        assert.equal(result.skippedBlocks[0].url, PRODUCT_URL);
        assert.ok(result.skippedBlocks[0].reason.length > 0);
    });

    it('recovers name and SKU from other blocks when the images block is missing', () => {
        const logger = createRecordingLogger();
        const cacheWithoutImages = omitBlock(buildFullGraphqlCache(), PRODUCT_IMAGES_GRAPHQL_ID);

        const result = parseProductDetail(cacheWithoutImages as never, PRODUCT_URL, logger);

        assert.ok(result.detail);
        assert.equal(result.detail.name, 'Jersey dress - coral option');
        assert.equal(result.detail.sku, 'AN621C1XR-G11');
        // Everything the images block owns is null, including arrays
        assert.equal(result.detail.brand, null);
        assert.equal(result.detail.flags, null);
        assert.equal(result.detail.images, null);
        assert.equal(result.detail.videos, null);
        assert.equal(result.detail.thumbnail, null);
        assert.equal(result.detail.comingSoon, null);
        // The other blocks are unaffected
        assert.deepEqual(result.detail.attributeCategories, expectedFullDetail().attributeCategories);
        assert.equal(result.skippedBlocks.length, 1);
        assert.equal(result.skippedBlocks[0].name, PRODUCT_IMAGES_BLOCK_NAME);
    });

    it('still saves a product when only the SKU is recoverable and name is absent everywhere', () => {
        const logger = createRecordingLogger();
        const cache = buildFullGraphqlCache();
        // Drop the images block (primary name/sku source), then strip names out of the remaining blocks
        const withoutImages = omitBlock(cache, PRODUCT_IMAGES_GRAPHQL_ID);
        for (const entry of Object.values(withoutImages)) {
            const product = (entry as { data?: { product?: { name?: string } } }).data?.product;
            if (product) {
                delete product.name;
            }
            const context = (entry as { data?: { context?: { name?: string } } }).data?.context;
            if (context) {
                delete context.name;
            }
        }

        const result = parseProductDetail(withoutImages as never, PRODUCT_URL, logger);

        assert.ok(result.detail, 'a SKU alone is enough to keep the product');
        assert.equal(result.detail.name, null);
        assert.equal(result.detail.sku, 'AN621C1XR-G11');
    });
});

describe('parseProductDetail - entirely missing GraphQL data', () => {
    it('returns no record for an empty cache and logs every skipped block with the URL', () => {
        const logger = createRecordingLogger();

        const result = parseProductDetail({}, PRODUCT_URL, logger);

        assert.equal(result.detail, null);
        assert.equal(result.skippedBlocks.length, 5);
        assert.deepEqual(
            result.skippedBlocks.map((block) => block.name).sort(),
            [
                PRODUCT_IMAGES_BLOCK_NAME,
                PRODUCT_ATTRIBUTES_BLOCK_NAME,
                GENERAL_PRODUCT_INFO_BLOCK_NAME,
                COLOR_PRICE_CATEGORY_BLOCK_NAME,
                PRICE_CURRENCY_BLOCK_NAME,
            ].sort(),
        );
        for (const block of result.skippedBlocks) {
            assert.equal(block.url, PRODUCT_URL);
            assert.ok(block.reason.length > 0);
        }

        const invalidWarning = logger.entries.find((entry) => entry.message.includes('Invalid product'));
        assert.ok(invalidWarning, 'the invalid-product reason must be logged explicitly');
        assert.deepEqual(invalidWarning?.data?.url, PRODUCT_URL);
    });

    it('returns no record when every present block is malformed', () => {
        const logger = createRecordingLogger();
        const cache: Record<string, unknown> = {};
        for (const graphqlId of [
            PRODUCT_IMAGES_GRAPHQL_ID,
            PRODUCT_ATTRIBUTES_GRAPHQL_ID,
            GENERAL_PRODUCT_INFO_GRAPHQL_ID,
            COLOR_PRICE_CATEGORY_GRAPHQL_ID,
            PRICE_CURRENCY_GRAPHQL_ID,
        ]) {
            cache[cacheKey(graphqlId)] = buildMalformedBlock();
        }

        const result = parseProductDetail(cache as never, PRODUCT_URL, logger);

        assert.equal(result.detail, null);
        assert.equal(result.skippedBlocks.length, 5);
    });
});
