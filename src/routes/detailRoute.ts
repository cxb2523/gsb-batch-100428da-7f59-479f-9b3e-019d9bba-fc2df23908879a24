import { Actor } from 'apify';
import { CheerioCrawlingContext } from 'crawlee';
import { GRAPHQL_PRODUCT_DATA_SEL } from '../constants.js';
import { parseGraphqlCache } from '../utils.js';
import { GraphqlProductData } from '../types/responses/graphql-product.js';
import { CrawleeState } from '../types/crawlee-state.js';
import { parseProductDetail } from './detail-parser.js';

export const detailRoute = async (context: CheerioCrawlingContext) => {
    const { crawler, request: { url }, $, log } = context;

    const title = $('head title').text();
    log.info(`${title}`, { url });

    let graphqlCache: Record<string, GraphqlProductData>;
    try {
        graphqlCache = parseGraphqlCache<GraphqlProductData>($, GRAPHQL_PRODUCT_DATA_SEL, url);
    } catch (err) {
        // An unreadable embedded cache means none of the blocks can be parsed; treat it like an invalid product
        log.warning(
            `Could not read the embedded GraphQL cache (${(err as Error).message}), skipping the product without saving`,
            { url },
        );
        return;
    }

    const { detail, skippedBlocks } = parseProductDetail(graphqlCache, url, log);

    // Neither name nor SKU is available: invalid product, do not write it and do not consume the quota
    if (!detail) {
        return;
    }

    const state = await crawler.useState<CrawleeState>();

    if (state.remainingItems <= 0) {
        return;
    }

    state.remainingItems--;

    if (skippedBlocks.length > 0) {
        log.warning(
            `Saved the product with ${skippedBlocks.length} GraphQL block(s) skipped: ${skippedBlocks
                .map((block) => block.name).join(', ')}`,
            {
                url,
                skippedBlocks: skippedBlocks.map((block) => ({ name: block.name, url: block.url, reason: block.reason })),
            },
        );
    }

    await Actor.pushData(detail);
};
