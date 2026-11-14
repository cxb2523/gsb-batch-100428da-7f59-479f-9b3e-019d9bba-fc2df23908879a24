import {
    GENERAL_PRODUCT_INFO_GRAPHQL_ID,
    PRICE_CURRENCY_GRAPHQL_ID,
    PRODUCT_ATTRIBUTES_GRAPHQL_ID,
    PRODUCT_IMAGES_GRAPHQL_ID,
    COLOR_PRICE_CATEGORY_GRAPHQL_ID,
} from '../../src/constants.js';
import { GraphqlProductData } from '../../src/types/responses/graphql-product.js';

/**
 * Real Zalando cache keys look like
 * `dedupe:query:9c94feddeb4251c3dc765f9507f692789449dde599bc5ec57fd2d26211739d64:v1:{"brandKey":...}`.
 * The parsers match by `key.includes(graphqlId)`, so the key only has to contain the block ID.
 */
export const cacheKey = (graphqlId: string) => `dedupe:query:${graphqlId}:v1:{"variables":{}}`;

export const PRODUCT_URL = 'https://www.zalando.co.uk/anna-field-jersey-dress-coral-option-an621c1xr-g11.html';

export const buildImagesBlock = () => ({
    data: {
        product: {
            id: 'AN621C1XR-G11',
            name: 'Jersey dress - coral option',
            sku: 'AN621C1XR-G11',
            uri: PRODUCT_URL,
            comingSoon: false,
            brand: {
                name: 'Anna Field by Zalando',
                uri: 'https://www.zalando.co.uk/anna-field/',
            },
            flags: [
                { formatted: 'Top rated25', kind: 'TOP_RATED', info: null },
                { formatted: '-9%25', kind: 'DEAL', info: null },
            ],
            gallery: [
                {
                    galleryThumbnails: {
                        __typename: 'Image',
                        uri: 'https://img01.ztat.net/article/thumb/1586540b69234154b9c13f0a341eb349.jpg?imwidth=156',
                    },
                    galleryMedia: {
                        __typename: 'Image',
                        uri: 'https://img01.ztat.net/article/full/1586540b69234154b9c13f0a341eb349.jpg?imwidth=1560',
                    },
                },
                {
                    galleryThumbnails: {
                        __typename: 'Image',
                        uri: 'https://img01.ztat.net/article/thumb/5e3f19c0d69b4cc68838c10f956d67ee.jpg?imwidth=156',
                    },
                    galleryMedia: {
                        __typename: 'Image',
                        uri: 'https://img01.ztat.net/article/full/5e3f19c0d69b4cc68838c10f956d67ee.jpg',
                    },
                },
                {
                    galleryThumbnails: {
                        __typename: 'VideoStream',
                        uri: 'https://video01.ztat.net/thumb.jpg',
                    },
                    galleryMedia: {
                        __typename: 'VideoStream',
                        uri: 'https://video01.ztat.net/b53eb71b-3425-4b38-99612185cc6bbae9/cmaf/e02ec503990e495094908f5329e31f6c.m3u8',
                    },
                },
            ],
            simplesWithStock: [],
        },
    },
});

export const buildAttributesBlock = () => ({
    data: {
        product: {
            id: 'AN621C1XR-G11',
            name: 'Jersey dress - coral option',
            sku: 'AN621C1XR-G11',
            sustainability: null,
            attributeSuperClusters: [
                {
                    id: 'material_care',
                    label: 'Material & care',
                    __typename: 'ProductAttributeDefaultSuperCluster',
                    clusters: [
                        {
                            sustainabilityClusterKind: null,
                            description: null,
                            label: null,
                            attributes: [
                                { __typename: 'ProductAttributeKeyValue', key: 'Outer fabric material', value: '98%25 polyester, 2%25 elastane' },
                                { __typename: 'ProductAttributeKeyValue', key: 'Fabric', value: 'Jersey' },
                                { __typename: 'ProductAttributeKeyValue', key: 'Empty one', value: 'undefined' },
                            ],
                        },
                    ],
                },
                {
                    id: 'empty_cluster',
                    label: 'Empty cluster',
                    __typename: 'ProductAttributeDefaultSuperCluster',
                    clusters: [
                        {
                            sustainabilityClusterKind: null,
                            description: null,
                            label: null,
                            attributes: [],
                        },
                    ],
                },
            ],
            brand: {
                name: 'Anna Field by Zalando',
                id: 'brand-1',
                followStatus: null,
            },
            brandRestriction: {
                themeOverride: null,
            },
            condition: null,
        },
    },
});

export const buildGeneralInfoBlock = () => ({
    data: {
        context: {
            entity_id: 'AN621C1XR-G11',
            isReviewable: null,
            hasSample: false,
            availabilityStatus: 'AVAILABLE',
            purchaseRestriction: {
                isInviteOnlyPurchase: null,
            },
            flags: [],
            brand: {
                name: 'Anna Field by Zalando',
                entity_id: 'brand-1',
            },
            color: {
                name: 'coral option',
            },
            display_price: {
                current: { amount: 3100 },
                original: { amount: 3399 },
                promotional: { amount: 3100 },
                displayMode: 'PROMOTIONAL',
            },
            navigationTargetGroup: 'WOMEN',
            name: 'Jersey dress - coral option',
            sizeAdvice: { recommendedOffset: 'SMALLER_SIZE' },
            simples: [
                {
                    sku: 'AN621C1XR-G1100XS000',
                    size: 'XS',
                    deliveryOptions: null,
                    offer: {
                        price: {
                            promotional: { amount: 3100 },
                            original: { amount: 3399 },
                            previous: null,
                            displayMode: 'PROMOTIONAL',
                        },
                        merchant: { id: 'merchant-1' },
                        selectionContext: null,
                        isMeaningfulOffer: true,
                        displayFlags: [],
                        stock: { quantity: 'OUT_OF_STOCK' },
                    },
                },
                {
                    sku: 'AN621C1XR-G11000S000',
                    size: 'S',
                    deliveryOptions: null,
                    offer: {
                        price: {
                            promotional: { amount: 3100 },
                            original: { amount: 3399 },
                            previous: null,
                            displayMode: 'PROMOTIONAL',
                        },
                        merchant: { id: 'merchant-1' },
                        selectionContext: null,
                        isMeaningfulOffer: true,
                        displayFlags: [],
                        stock: { quantity: 'MANY' },
                    },
                },
            ],
        },
    },
});

export const buildColorPriceCategoryBlock = () => ({
    data: {
        product: {
            id: 'AN621C1XR-G11',
            sku: 'AN621C1XR-G11',
            brand: {
                name: 'Anna Field by Zalando',
            },
            color: {
                name: 'coral option',
                label: 'coral',
            },
            displayPrice: {
                original: {
                    formatted: '£33.99',
                },
                promotional: {
                    formatted: '£31.00',
                },
            },
            category: 'Jersey dress',
        },
    },
});

export const buildPriceCurrencyBlock = () => ({
    data: {
        product: {
            simples: [
                {
                    size: 'XS',
                    sku: 'AN621C1XR-G1100XS000',
                    offer: {
                        stock: {
                            quantity: 'OUT_OF_STOCK',
                        },
                        price: {
                            original: {
                                amount: 3399,
                                currency: 'GBP',
                            },
                            promotional: {
                                amount: 3100,
                                currency: 'GBP',
                            },
                        },
                    },
                },
            ],
        },
    },
});

export const buildFullGraphqlCache = (): Record<string, GraphqlProductData> => ({
    [cacheKey(PRODUCT_IMAGES_GRAPHQL_ID)]: buildImagesBlock() as GraphqlProductData,
    [cacheKey(PRODUCT_ATTRIBUTES_GRAPHQL_ID)]: buildAttributesBlock() as GraphqlProductData,
    [cacheKey(GENERAL_PRODUCT_INFO_GRAPHQL_ID)]: buildGeneralInfoBlock() as GraphqlProductData,
    [cacheKey(COLOR_PRICE_CATEGORY_GRAPHQL_ID)]: buildColorPriceCategoryBlock() as GraphqlProductData,
    [cacheKey(PRICE_CURRENCY_GRAPHQL_ID)]: buildPriceCurrencyBlock() as GraphqlProductData,
});

/** Block with the right cache key but an unexpected payload shape, simulating a page redesign */
export const buildMalformedBlock = () => ({
    data: {
        somethingUnexpected: true,
    },
});
