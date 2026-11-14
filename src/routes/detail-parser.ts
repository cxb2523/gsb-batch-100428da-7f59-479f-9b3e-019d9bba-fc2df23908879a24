import {
    GENERAL_PRODUCT_INFO_GRAPHQL_ID,
    PRICE_CURRENCY_GRAPHQL_ID,
    PRODUCT_ATTRIBUTES_GRAPHQL_ID,
    PRODUCT_IMAGES_GRAPHQL_ID,
    COLOR_PRICE_CATEGORY_GRAPHQL_ID,
    PRODUCT_IMAGES_DATA_REGEX,
    PRODUCT_ATTRIBUTES_DATA_REGEX,
    GENERAL_PRODUCT_INFO_DATA_REGEX,
    COLOR_PRICE_CATEGORY_DATA_REGEX,
    PRICE_CURRENCY_DATA_REGEX,
    PRODUCT_IMAGES_BLOCK_NAME,
    PRODUCT_ATTRIBUTES_BLOCK_NAME,
    GENERAL_PRODUCT_INFO_BLOCK_NAME,
    COLOR_PRICE_CATEGORY_BLOCK_NAME,
    PRICE_CURRENCY_BLOCK_NAME,
} from '../constants.js';
import { parseRelevantGraphqlData } from '../utils.js';
import {
    GraphqlProductImagesAndSimples,
    GraphqlProductData,
    GraphqlProductAttributes,
    Attribute,
    GraphqlGeneralProductInfo,
    GraphqlColorPriceCategory,
    GraphqlPriceCurrency,
    StockStatus,
} from '../types/responses/graphql-product.js';

/** Subset of the Apify `Log` interface this module relies on, so tests can pass a lightweight fake */
export type DetailParseLogger = {
    info: (message: string, data?: Record<string, unknown>) => void;
    warning: (message: string, data?: Record<string, unknown>) => void;
    error: (message: string, data?: Record<string, unknown>) => void;
    debug: (message: string, data?: Record<string, unknown>) => void;
};

export type SkippedBlock = {
    /** Human-readable GraphQL block name, see constants.ts */
    name: string;
    /** Product page URL the block was read from */
    url: string;
    /** Why the block could not be parsed */
    reason: string;
};

export type ProductDetailRecord = {
    url: string;
    name: string | null;
    sku: string | null;
    brand: { name: string; uri: string } | null;
    flags: { formatted: string; kind: string; info: string | null }[] | null;
    comingSoon: boolean | null | undefined;
    thumbnail: string | null | undefined;
    images: string[] | null;
    videos: string[] | null;
    category: string | null;
    color: { name: string; label: string } | null;
    formattedPrice: { original: string; current: string; promotional: string | null } | null;
    priceCurrency: string | null;
    price: { original: number | null; current: number | null; promotional: number | null } | null;
    sizes: { size: string; sku: string; stockStatus: StockStatus }[] | null;
    available: boolean | null;
    sizeAdvice: string | null;
    navigationTargetGroup: string | null;
    condition: string | null;
    attributeCategories: {
        categoryId: string;
        categoryName: string;
        attributes: { key: string; value: string }[];
    }[] | null;
};

export type ProductDetailParseResult = {
    /** Null when neither product name nor SKU could be read from any block */
    detail: ProductDetailRecord | null;
    /** GraphQL blocks that were missing or malformed, reported for layout-change debugging */
    skippedBlocks: SkippedBlock[];
};

type ImagesBlockFields = {
    name: string;
    sku: string;
    brand: { name: string; uri: string };
    flags: { formatted: string; kind: string; info: string | null }[];
    comingSoon: boolean | undefined;
    thumbnail: string | undefined;
    images: string[];
    videos: string[];
};

type AttributesBlockFields = {
    condition: string | null;
    attributeCategories: {
        categoryId: string;
        categoryName: string;
        attributes: { key: string; value: string }[];
    }[];
};

type GeneralInfoBlockFields = {
    price: { original: number | null; current: number | null; promotional: number | null };
    sizes: { size: string; sku: string; stockStatus: StockStatus }[];
    available: boolean;
    sizeAdvice: string | null;
    navigationTargetGroup: string;
};

type ColorBlockFields = {
    category: string;
    color: { name: string; label: string };
    formattedPrice: { original: string; current: string; promotional: string | null };
};

type GraphqlBlock<T> = {
    name: string;
    parse: (graphqlCache: Record<string, GraphqlProductData>) => T;
};

type ImagesBlockNullFields = Pick<ProductDetailRecord,
    'name' | 'sku' | 'brand' | 'flags' | 'comingSoon' | 'thumbnail' | 'images' | 'videos'>;
type ColorBlockNullFields = Pick<ProductDetailRecord, 'category' | 'color' | 'formattedPrice'>;
type GeneralInfoBlockNullFields = Pick<ProductDetailRecord,
    'price' | 'sizes' | 'available' | 'sizeAdvice' | 'navigationTargetGroup'>;
type AttributesBlockNullFields = Pick<ProductDetailRecord, 'condition' | 'attributeCategories'>;

/** Throws a descriptive error when no cache entry matched the block, so the skip reason is actionable */
const getFirstBlockEntry = <T>(entries: T[], blockName: string): T => {
    const entry = entries[0];
    if (!entry) {
        throw new Error(`GraphQL block "${blockName}" was not found in the page cache`);
    }
    return entry;
};

const parseImagesAndSimpleInfo = (
    graphqlCache: Record<string, GraphqlProductData>,
    graphqlId: string,
    graphqlData: RegExp,
    blockName: string,
): ImagesBlockFields => {
    const productImages = getFirstBlockEntry(
        parseRelevantGraphqlData(
            graphqlCache,
            graphqlId,
            graphqlData,
        ) as GraphqlProductImagesAndSimples[],
        blockName,
    );

    const { product } = productImages.data;

    const gallery = product.gallery.map(({ galleryMedia }) => galleryMedia.uri.replace(/\?.+$/, ''));

    const images = gallery.filter((imageUrl) => !imageUrl.match(/\.(mp4|avi|m3u8)/));
    const videos = gallery.filter((videoUrl) => videoUrl.match(/\.(mp4|avi|m3u8)/));

    const thumbnail = product.gallery
        // eslint-disable-next-line no-underscore-dangle
        .find(({ galleryThumbnails }) => galleryThumbnails.__typename === 'Image')?.galleryThumbnails.uri;

    const { name, sku, brand, comingSoon } = product;

    const flags = product.flags.map((flag) => ({
        ...flag,
        formatted: flag.formatted.replace(/25$/, ''),
    }));

    return { name, sku, brand, flags, comingSoon, thumbnail, images, videos };
};

/** Values are no longer always URI encoded, such as '64% cotton' */
const tryDecodeURIComponent = (value: string) => {
    try {
        return decodeURIComponent(value);
    } catch {
        return String(value);
    }
};

const parseAttributes = (attributes: Attribute[]) => {
    return attributes.map((attribute) => ({
        key: attribute.key,
        value: tryDecodeURIComponent(attribute.value).replace(/^undefined$/i, ''),
    })).filter((attribute) => attribute.value);
};

const parseProductAttributes = (
    graphqlCache: Record<string, GraphqlProductData>,
    graphqlId: string,
    graphqlData: RegExp,
    blockName: string,
): AttributesBlockFields => {
    const productAttributes = getFirstBlockEntry(
        parseRelevantGraphqlData(
            graphqlCache,
            graphqlId,
            graphqlData,
        ) as GraphqlProductAttributes[],
        blockName,
    );

    const { product: { condition, attributeSuperClusters } } = productAttributes.data;

    const attributeCategories = attributeSuperClusters.map((superCluster) => {
        const attributes: {
            key: string;
            value: string;
        }[] = [];

        superCluster.clusters.forEach((cluster) => {
            attributes.push(...parseAttributes(cluster.attributes ?? []));
        });

        return {
            categoryId: superCluster.id,
            categoryName: superCluster.label,
            attributes,
        };
    }).filter((category) => category.attributes.length > 0);

    return { condition, attributeCategories };
};

const parseProductPriceAmount = (amount: number) => amount / 100;

const parseGeneralProductInfo = (
    graphqlCache: Record<string, GraphqlProductData>,
    graphqlId: string,
    graphqlData: RegExp,
    blockName: string,
): GeneralInfoBlockFields => {
    const productInfo = getFirstBlockEntry(
        parseRelevantGraphqlData(
            graphqlCache,
            graphqlId,
            graphqlData,
        ) as GraphqlGeneralProductInfo[],
        blockName,
    );

    const {
        availabilityStatus, sizeAdvice, display_price: price, navigationTargetGroup, simples,
    } = productInfo.data.context;

    const sizes = simples.map(({ size, sku, offer }) => ({
        size,
        sku,
        stockStatus: offer.stock.quantity,
    }));

    return {
        price: {
            original: price.original ? parseProductPriceAmount(price.original.amount) : null,
            current: price.current ? parseProductPriceAmount(price.current.amount) : null,
            promotional: price.promotional ? parseProductPriceAmount(price.promotional.amount) : null,
        },
        sizes,
        available: availabilityStatus === 'AVAILABLE',
        sizeAdvice: sizeAdvice ? sizeAdvice.recommendedOffset : null,
        navigationTargetGroup,
    };
};

const parsePriceCurrency = (
    graphqlCache: Record<string, GraphqlProductData>,
    graphqlId: string,
    graphqlData: RegExp,
    blockName: string,
): string | null => {
    const entries = parseRelevantGraphqlData(
        graphqlCache,
        graphqlId,
        graphqlData,
    ) as GraphqlPriceCurrency[];

    // A missing block is reported as skipped; a present block without size offers still yields null
    if (entries.length === 0) {
        throw new Error(`GraphQL block "${blockName}" was not found in the page cache`);
    }

    const simples = entries[0]?.data.product.simples ?? [];

    return simples[0] ? simples[0].offer.price.original.currency : null;
};

const parseColorPriceCategory = (
    graphqlCache: Record<string, GraphqlProductData>,
    graphqlId: string,
    graphqlData: RegExp,
    blockName: string,
): ColorBlockFields => {
    const colorPriceCategory = getFirstBlockEntry(
        parseRelevantGraphqlData(
            graphqlCache,
            graphqlId,
            graphqlData,
        ) as GraphqlColorPriceCategory[],
        blockName,
    );

    const { color, displayPrice, category } = colorPriceCategory.data.product;
    const { original: originalPrice, promotional: promotionalPrice } = displayPrice;

    return {
        category,
        color,
        formattedPrice: {
            original: originalPrice.formatted,
            current: promotionalPrice ? promotionalPrice.formatted : originalPrice.formatted,
            promotional: promotionalPrice ? promotionalPrice.formatted : null,
        },
    };
};

const IMAGES_BLOCK: GraphqlBlock<ImagesBlockFields> = {
    name: PRODUCT_IMAGES_BLOCK_NAME,
    parse: (graphqlCache) => parseImagesAndSimpleInfo(
        graphqlCache,
        PRODUCT_IMAGES_GRAPHQL_ID,
        PRODUCT_IMAGES_DATA_REGEX,
        PRODUCT_IMAGES_BLOCK_NAME,
    ),
};

const ATTRIBUTES_BLOCK: GraphqlBlock<AttributesBlockFields> = {
    name: PRODUCT_ATTRIBUTES_BLOCK_NAME,
    parse: (graphqlCache) => parseProductAttributes(
        graphqlCache,
        PRODUCT_ATTRIBUTES_GRAPHQL_ID,
        PRODUCT_ATTRIBUTES_DATA_REGEX,
        PRODUCT_ATTRIBUTES_BLOCK_NAME,
    ),
};

const GENERAL_INFO_BLOCK: GraphqlBlock<GeneralInfoBlockFields> = {
    name: GENERAL_PRODUCT_INFO_BLOCK_NAME,
    parse: (graphqlCache) => parseGeneralProductInfo(
        graphqlCache,
        GENERAL_PRODUCT_INFO_GRAPHQL_ID,
        GENERAL_PRODUCT_INFO_DATA_REGEX,
        GENERAL_PRODUCT_INFO_BLOCK_NAME,
    ),
};

const COLOR_PRICE_CATEGORY_BLOCK: GraphqlBlock<ColorBlockFields> = {
    name: COLOR_PRICE_CATEGORY_BLOCK_NAME,
    parse: (graphqlCache) => parseColorPriceCategory(
        graphqlCache,
        COLOR_PRICE_CATEGORY_GRAPHQL_ID,
        COLOR_PRICE_CATEGORY_DATA_REGEX,
        COLOR_PRICE_CATEGORY_BLOCK_NAME,
    ),
};

const PRICE_CURRENCY_BLOCK: GraphqlBlock<string | null> = {
    name: PRICE_CURRENCY_BLOCK_NAME,
    parse: (graphqlCache) => parsePriceCurrency(
        graphqlCache,
        PRICE_CURRENCY_GRAPHQL_ID,
        PRICE_CURRENCY_DATA_REGEX,
        PRICE_CURRENCY_BLOCK_NAME,
    ),
};

const IMAGES_BLOCK_NULL_FIELDS: ImagesBlockNullFields = {
    name: null,
    sku: null,
    brand: null,
    flags: null,
    comingSoon: null,
    thumbnail: null,
    images: null,
    videos: null,
};

const COLOR_BLOCK_NULL_FIELDS: ColorBlockNullFields = {
    category: null,
    color: null,
    formattedPrice: null,
};

const GENERAL_INFO_BLOCK_NULL_FIELDS: GeneralInfoBlockNullFields = {
    price: null,
    sizes: null,
    available: null,
    sizeAdvice: null,
    navigationTargetGroup: null,
};

const ATTRIBUTES_BLOCK_NULL_FIELDS: AttributesBlockNullFields = {
    condition: null,
    attributeCategories: null,
};

/** Runs one block parser in isolation: a missing/malformed block yields null instead of failing the product */
const runBlock = <T>(
    graphqlCache: Record<string, GraphqlProductData>,
    block: GraphqlBlock<T>,
    url: string,
    log: DetailParseLogger,
    skippedBlocks: SkippedBlock[],
): T | null => {
    try {
        return block.parse(graphqlCache);
    } catch (err) {
        const reason = (err as Error).message;
        log.warning(`Skipping GraphQL block "${block.name}": ${reason}`, { block: block.name, url });
        skippedBlocks.push({ name: block.name, url, reason });
        return null;
    }
};

type NameAndSkuEntry = {
    data?: {
        product?: { name?: unknown; sku?: unknown };
        context?: { name?: unknown };
    };
};

const isNonEmptyString = (value: unknown): value is string => typeof value === 'string' && value.length > 0;

/**
 * Recovery path for name/SKU when the images block (their primary source) is missing.
 * Never throws: entries that do not match the expected shape are simply ignored.
 */
const resolveNameAndSku = (graphqlCache: Record<string, GraphqlProductData>) => {
    let name: string | null = null;
    let sku: string | null = null;

    Object.values(graphqlCache).forEach((rawEntry) => {
        const entry = rawEntry as NameAndSkuEntry;
        const product = entry.data?.product;
        const context = entry.data?.context;

        if (!name) {
            const candidateName = product?.name ?? context?.name;
            if (isNonEmptyString(candidateName)) {
                name = candidateName;
            }
        }

        if (!sku) {
            const candidateSku = product?.sku;
            if (isNonEmptyString(candidateSku)) {
                sku = candidateSku;
            }
        }
    });

    return { name, sku };
};

/**
 * Parses a product detail page GraphQL cache with per-block fault tolerance.
 *
 * Each GraphQL block is parsed independently: a missing or malformed block only nulls
 * the fields it provides (arrays become null, matching the dataset schema), while the
 * remaining blocks still populate the record. A product without any readable name or
 * SKU is treated as invalid: no record is produced, so the caller can skip quota usage.
 */
export const parseProductDetail = (
    graphqlCache: Record<string, GraphqlProductData>,
    url: string,
    log: DetailParseLogger,
): ProductDetailParseResult => {
    const skippedBlocks: SkippedBlock[] = [];

    const simpleInfo = runBlock(graphqlCache, IMAGES_BLOCK, url, log, skippedBlocks);
    const colorPriceCategory = runBlock(graphqlCache, COLOR_PRICE_CATEGORY_BLOCK, url, log, skippedBlocks);
    const generalInfo = runBlock(graphqlCache, GENERAL_INFO_BLOCK, url, log, skippedBlocks);
    const attributes = runBlock(graphqlCache, ATTRIBUTES_BLOCK, url, log, skippedBlocks);
    const priceCurrency = runBlock(graphqlCache, PRICE_CURRENCY_BLOCK, url, log, skippedBlocks);

    let name = simpleInfo?.name ?? null;
    let sku = simpleInfo?.sku ?? null;

    // The images block is the primary name/SKU source; recover them from other blocks when it is missing
    if (!name || !sku) {
        const fallback = resolveNameAndSku(graphqlCache);
        name = name ?? fallback.name;
        sku = sku ?? fallback.sku;
    }

    if (!name && !sku) {
        log.warning(
            'Invalid product: neither name nor SKU could be read from any GraphQL block, skipping without saving',
            { url, skippedBlocks },
        );
        return { detail: null, skippedBlocks };
    }

    const detail: ProductDetailRecord = {
        url,
        ...(simpleInfo ?? IMAGES_BLOCK_NULL_FIELDS),
        ...(colorPriceCategory ?? COLOR_BLOCK_NULL_FIELDS),
        priceCurrency: priceCurrency ?? null,
        ...(generalInfo ?? GENERAL_INFO_BLOCK_NULL_FIELDS),
        ...(attributes ?? ATTRIBUTES_BLOCK_NULL_FIELDS),
        name,
        sku,
    };

    return { detail, skippedBlocks };
};
