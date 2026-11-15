'use strict';

const cheerio = require('cheerio');
const writeDataToCsv = require('./utils').writeDataToCsv;
const writeDataToXls = require('./utils').writeDataToXls;
const cliProgress = require('cli-progress');
const querystring = require('querystring');
const retry = require('promise-retry');
const ScrapingAntClient = require('@scrapingant/scrapingant-client')


const CONSTANTS = require('./constants');

const DETAIL_STATUS = CONSTANTS.productDetail.statuses;
const DETAIL_STATUS_FIELD = CONSTANTS.productDetail.statusField;
const DETAIL_REASON_FIELD = CONSTANTS.productDetail.reasonField;

const STOP_REASONS = CONSTANTS.stopReasons;

/**
 * startScraping() historically returned a plain array of products. To keep that contract while
 * exposing the run outcome, the array carries non-enumerable metadata: JSON serialisation and
 * iteration/for-of still see only the products, while result.summary and result.products work.
 */
const buildResultObject = (products, summary) => {
    const result = products;

    Object.defineProperty(result, 'products', { value: products, enumerable: false, writable: true, configurable: true });
    Object.defineProperty(result, 'keyword', { value: summary.keyword, enumerable: false, writable: true, configurable: true });
    Object.defineProperty(result, 'summary', { value: summary, enumerable: false, writable: true, configurable: true });
    Object.defineProperty(result, 'retry', { value: null, enumerable: false, writable: true, configurable: true });

    return result;
};

class ProductsScraper {

    constructor({keyword, number, host, apiKey, save, country, fileType, showProgress, client, retryOptions}) {
        this.host = `https://${host || CONSTANTS.defaultAmazonUrl}`;

        this.alreadyScrappedProducts = {};
        this.apiKey = apiKey;
        this.keyword = keyword;
        this.fileType = fileType;
        this.numberOfProducts = parseInt(number) || CONSTANTS.defaultItemLimit;
        this.currentSearchPage = 1;
        this.saveToFile = save || false;
        this.country = country;
        this.progressBar = showProgress ? new cliProgress.SingleBar({
            format: `Amazon Scraping: ${this.keyword} | {bar} | {percentage}% - {value}/{total} Products || ETA: {eta}s`,
        }, cliProgress.Presets.shades_classic) : null;
        this.productsPromises = [];

        // A scraping client can be injected (used by tests or by callers with their own HTTP layer).
        // When it is not provided, the official ScrapingAnt client is created from the API key, so
        // that a missing key still produces the friendly validation error below.
        this.injectedClient = client || null;
        this.client = this.injectedClient || (apiKey ? new ScrapingAntClient({ apiKey }) : null);

        // promise-retry options. Only timing options may be supplied (mostly for tests);
        // the retry count always comes from constants.js and can not be overridden.
        const allowedRetryOptions = {};
        ['minTimeout', 'maxTimeout', 'factor', 'randomize'].forEach(key => {
            if (retryOptions && retryOptions[key] !== undefined) {
                allowedRetryOptions[key] = retryOptions[key];
            }
        });
        this.retryOptions = Object.assign({}, allowedRetryOptions, { retries: CONSTANTS.limit.retry });

        this.stopReason = null;
        this.searchPageEmpty = false;
        this.searchPageFailed = false;
        this.searchPageFailureReason = '';
        this.progressActive = false;
    }

    async startScraping() {
        this.checkForCountry();
        this.checkForApiKey();
        this.checkForKeyword();
        this.checkForFileType();
        this.checkForProductsNumber();

        this.stopReason = null;
        this.searchPageEmpty = false;
        this.searchPageFailed = false;
        this.searchPageFailureReason = '';

        // A new run never reuses products or page state from a previous run on the same instance.
        this.alreadyScrappedProducts = {};
        this.productsPromises = [];
        this.currentSearchPage = 1;

        if (this.progressBar) {
            this.progressBar.start(this.numberOfProducts, 0);
            this.progressActive = true;
        }

        while (true) {
            if (Object.keys(this.alreadyScrappedProducts).length >= this.numberOfProducts) {
                this.stopReason = STOP_REASONS.productLimitReached;
                break;
            }

            const currentPageProducts = await this.getCurrentPageData();
            const productIds = Object.keys(currentPageProducts);

            if (productIds.length > 0) {
                this.alreadyScrappedProducts = Object.assign(currentPageProducts, this.alreadyScrappedProducts);

                this.productsPromises = this.productsPromises.concat(productIds.map(this.getProductPageData.bind(this)))

                this.currentSearchPage++;
            } else {
                // The search page returned no products - stop scraping and report why.
                // `searchPageEmpty` is only true when the whole run got nothing at all.
                const hasScrapedProducts = Object.keys(this.alreadyScrappedProducts).length > 0;
                this.searchPageEmpty = !hasScrapedProducts;
                this.stopReason = this.searchPageFailed
                    ? STOP_REASONS.searchPageFailed
                    : (hasScrapedProducts ? STOP_REASONS.searchResultsExhausted : STOP_REASONS.searchPageNoResults);
                break;
            }
        }

        //Waiting for all product promises completion
        await Promise.all(this.productsPromises)

        await this.checkAndSaveToFile();

        if (this.progressBar) {
            this.progressBar.stop();
        }
        this.progressActive = false;

        const result = this.buildResult();

        console.log(`Total scraped products count: ${result.products.length}`);
        if (result.summary.detailFailedCount > 0) {
            console.log(`${result.summary.detailFailedCount} product(s) failed to get detail data and were marked as failed. Use retryFailedProducts() to retry them.`);
        }
        if (result.summary.searchPageEmpty) {
            console.log(`Search page did not return any products: ${result.summary.searchPageFailureReason}`);
        } else if (result.summary.searchPageFailed) {
            console.log(`Stopped before reaching the requested product count: ${result.summary.searchPageFailureReason}`);
        }

        return result;
    }

    checkForCountry() {
        if (this.country) {
            this.country = this.country.toLowerCase();

            if (!CONSTANTS.supported_countries.includes(this.country)) {
                throw `Not supported country. Please use one from the following: ${CONSTANTS.supported_countries.join(", ")}`;
            }
        }
    }

    checkForApiKey() {
        if (!this.apiKey && !this.client) {
            throw `No ScrapingAnt apiKey. Please refer to https://scrapingant.com for getting yours.`;
        }
    }

    checkForKeyword() {
        if (!this.keyword) {
            throw `No keyword for search. Please specify it..`;
        }
    }

    checkForFileType() {
        if (this.fileType) {
            this.saveToFile = true;
            this.fileType = this.fileType.toLowerCase();

            if (!Object.values(CONSTANTS.supported_filetypes).includes(this.fileType)) {
                throw `Not supported file type. Please use one from the following: ${Object.values(CONSTANTS.supported_filetypes).join(", ")}`;
            }
        }
    }

    checkForProductsNumber() {
        if (this.numberOfProducts > CONSTANTS.limit.product) {
            this.numberOfProducts = CONSTANTS.limit.product;
            console.info(`Setting number to MAXIMUM available (${CONSTANTS.limit.product}) because of exceeding limit.`);
        }
    }

    async checkAndSaveToFile() {
        if (this.saveToFile && Object.keys(this.alreadyScrappedProducts).length > 0) {
            // Filename rule is unchanged: the search keyword with spaces replaced by underscores.
            // The fallback only applies when retrying details from a saved file without a keyword.
            const preparedKeyword = (this.keyword || 'products').replace(/\s/g, "_");

            const products = Object.values(this.alreadyScrappedProducts);

            if (this.fileType === CONSTANTS.supported_filetypes.csv) {
                await writeDataToCsv(preparedKeyword, products);
            }

            if (this.fileType === CONSTANTS.supported_filetypes.xls) {
                await writeDataToXls(preparedKeyword, products);
            }
        }
    }

    async getCurrentPageData() {
        const queryParams = querystring.encode({
            k: this.keyword,
            ...(this.currentSearchPage > 1 ? { page: this.currentSearchPage, ref: `sr_pg_${this.currentSearchPage}` } : {})
        });

        let lastError = null;

        // Retry for avoiding empty or detected result from Amazon
        for (let i = 0; i < CONSTANTS.limit.retry; i++) {
            try {
                // Retry for any network or accessibility cases
                const params = this.country ? { proxy_country: this.country } : {};
                const response = await retry((attempt) => this.client.scrape(
                    `${this.host}/s?${queryParams}`,
                    params
                ).catch(attempt), this.retryOptions);

                const pageBody = response ? response.content : '';

                if (typeof pageBody !== 'string') {
                    throw new Error('search response contained no page content');
                }

                const products = this.getProducts(pageBody);
                if (Object.keys(products).length > 0) {
                    this.searchPageFailureReason = '';
                    this.searchPageFailed = false;
                    return products;
                }
            } catch (err) {
                lastError = err;
                console.error(`Failed to get page ${this.currentSearchPage} for keyword ${this.keyword}. Going to retry...`);
            }
        }

        this.searchPageFailed = !!lastError;
        this.searchPageFailureReason = lastError
            ? `Failed to get search page ${this.currentSearchPage} for keyword ${this.keyword}: ${this.describeError(lastError)}`
            : `Search page ${this.currentSearchPage} returned no products for keyword ${this.keyword}`;

        return {};
    }

    getProducts(body) {
        const dom = cheerio.load(String(body || '').replace(/\s\s+/g, '').replace(/\n/g, ''));
        const productList = dom('div[data-index]');
        const scrapingResult = {};

        for (let i = 0; i < productList.length; i++) {
            const totalInResult = Object.keys(scrapingResult).length + Object.keys(this.alreadyScrappedProducts).length;
            if (totalInResult >= this.numberOfProducts) {
                break;
            }
            if (!productList[i].attribs['data-asin']) {
                continue;
            }
            scrapingResult[productList[i].attribs['data-asin']] = {
                'amazon-id': productList[i].attribs['data-asin'],
                'title': "",
                'thumbnail': "",
                'high-res-image': "",
                'url': "",
                'is-discounted': false,
                'is-sponsored': false,
                'is-amazon-choice': false,
                'price': "",
                'before-discount': "",
                'reviews-count': 0,
                'rating': 0,
                'score': 0,
                'savings': 0,
                'short-description': "",
                'full-description': "",
                [DETAIL_STATUS_FIELD]: DETAIL_STATUS.pending,
                [DETAIL_REASON_FIELD]: ""
            };
        }

        for (let key in scrapingResult) {
            try {
                const priceSearch = dom(`div[data-asin=${key}] span[data-a-size="l"]`)[0] || dom(`div[data-asin=${key}] span[data-a-size="m"]`)[0];
                const discountSearch = dom(`div[data-asin=${key}] span[data-a-strike="true"]`)[0];
                const ratingSearch = dom(`div[data-asin=${key}] .a-icon-star-small`)[0];
                const titleThumbnailSearch = dom(`div[data-asin=${key}] [data-image-source-density="1"]`)[0];
                const urlSearch = dom(`div[data-asin=${key}] .a-link-normal`);
                const amazonChoice = dom(`div[data-asin=${key}] span[id="${key}-amazons-choice"]`).text();

                if (priceSearch) {
                    scrapingResult[key].price = dom(priceSearch.children[0]).text().replace(/[^D+0-9.,]/g, '');
                }

                if (amazonChoice) {
                    scrapingResult[key]['is-amazon-choice'] = true;
                }

                if (discountSearch) {
                    scrapingResult[key]['before-discount'] = dom(discountSearch.children[0]).text().replace(/[^D+0-9.,]/g, '');
                    scrapingResult[key]['is-discounted'] = true;
                    let savings = scrapingResult[key]['before-discount'] - scrapingResult[key].price;
                    if (savings <= 0) {
                        scrapingResult[key]['is-discounted'] = false;
                        scrapingResult[key]['before-discount'] = 0;
                    } else {
                        scrapingResult[key].savings = savings;
                    }
                }

                if (ratingSearch) {
                    scrapingResult[key].rating = parseFloat(ratingSearch.children[0].children[0].data);
                    scrapingResult[key]['reviews-count'] = parseInt(ratingSearch.parent.parent.parent.next.attribs['aria-label'].replace(/,/g, ''));
                    scrapingResult[key].score = parseFloat(scrapingResult[key].rating * scrapingResult[key]['reviews-count']).toFixed(2);
                }

                if (titleThumbnailSearch) {
                    scrapingResult[key].title = titleThumbnailSearch.attribs.alt;
                    scrapingResult[key].thumbnail = titleThumbnailSearch.attribs.src;
                    scrapingResult[key]['high-res-image'] = scrapingResult[key].thumbnail.split("._")[0] + ".jpg"
                }

                if (urlSearch) {
                    let url = urlSearch[0].attribs.href;
                    if (url.indexOf('/gcx/-/') > -1) {
                        url = urlSearch[1].attribs.href;
                    }

                    if (url.indexOf('/gp/') > -1) {
                        scrapingResult[key]['is-sponsored'] = true;
                    }

                    scrapingResult[key].url = `${this.host}${url}`;
                }
            } catch (err) {
                console.error(err);
            }
        }

        return scrapingResult;
    }

    /**
     * Fetch short and full descriptions for one product, using the same retry budget as before.
     *
     * @param amazonId - also called asin, unique Amazon product ID
     * @returns {Promise<{success: boolean, shortDescription?: string, fullDescription?: string, failureReason?: string}>}
     */
    async fetchProductDetails(amazonId) {
        let failureReason = '';

        // Retry for avoiding empty or detected result from Amazon
        for (let i = 0; i < CONSTANTS.limit.retry; i++) {
            try {
                // Retry for any network or accessibility cases
                const response = await retry((attempt) => this.client.scrape(
                    `${this.host}/dp/${amazonId}`,
                    { proxy_country: this.country }
                    ).catch(attempt), this.retryOptions);

                if (!response || typeof response.content !== 'string') {
                    throw new Error('detail response contained no page content');
                }

                const pageBody = response.content;

                const dom = cheerio.load(pageBody.replace(/\s\s+/g, '').replace(/\n/g, ''));

                const shortDescription = dom(`#featurebullets_feature_div`).text();
                const fullDescription = dom(`#productDescription`).text();

                if (shortDescription || fullDescription) {
                    return { success: true, shortDescription, fullDescription };
                }

                // Page loaded but contained no descriptions. Remember it and retry.
                failureReason = `Detail page for product ${amazonId} returned no short or full description (attempt ${i + 1} of ${CONSTANTS.limit.retry})`;
            } catch (exception) {
                failureReason = `Detail page request for product ${amazonId} failed: ${this.describeError(exception)}`;
                console.error(`Failed to get product ${amazonId} for keyword ${this.keyword}. Going to retry...`);
            }
        }

        return { success: false, failureReason };
    }

    /**
     * Store the outcome of a detail page fetch on the product. A failed product keeps all fields
     * obtained from the search page and gets an explicit failed marker plus the latest failure reason.
     *
     * @param amazonId
     * @param details - result of fetchProductDetails
     * @returns {{success: boolean, amazonId: string, reason?: string}}
     */
    applyProductDetails(amazonId, details) {
        const product = this.alreadyScrappedProducts[amazonId];

        if (!product) {
            return { success: details.success, amazonId, reason: details.failureReason };
        }

        if (details.success) {
            product['short-description'] = details.shortDescription;
            product['full-description'] = details.fullDescription;
            product[DETAIL_STATUS_FIELD] = DETAIL_STATUS.success;
            product[DETAIL_REASON_FIELD] = '';

            if (this.progressBar && this.progressActive) {
                this.progressBar.increment();
            }

            return { success: true, amazonId };
        }

        // Keep the search-page fields untouched, but never pretend the empty description is a success.
        product['short-description'] = '';
        product['full-description'] = '';
        product[DETAIL_STATUS_FIELD] = DETAIL_STATUS.failed;
        // Overwrite (not append) so repeated retries always expose the latest failure only.
        product[DETAIL_REASON_FIELD] = details.failureReason;

        return { success: false, amazonId, reason: details.failureReason };
    }

    /**
     *
     * @param amazonId - also called asin, unique Amazon product ID
     * @returns {Promise<{success: boolean, amazonId: string, reason?: string}>}
     *
     * The main idea of this method is pretty simple - amend existing products object with additional data
     */
    async getProductPageData(amazonId) {
        const details = await this.fetchProductDetails(amazonId);
        return this.applyProductDetails(amazonId, details);
    }

    /**
     * Retry detail enrichment only for products marked as failed.
     *
     * Can be called right after startScraping() on the same instance, or later with a previously
     * obtained result object (or a plain array of product rows) to restore the failed products.
     * On success the failure marker is cleared; on another failure the marker and reason are
     * updated to the latest attempt without adding duplicate product entries.
     *
     * @param [source] - optional startScraping() result or an array of product objects
     * @returns {Promise<object>} same shape as startScraping() result plus `retry` details
     */
    async retryFailedProducts(source) {
        if (source) {
            this.loadProducts(source);
        }

        const failedIds = Object.keys(this.alreadyScrappedProducts).filter(amazonId =>
            this.alreadyScrappedProducts[amazonId][DETAIL_STATUS_FIELD] === DETAIL_STATUS.failed
        );

        if (this.progressBar && !this.progressActive && failedIds.length > 0) {
            this.progressBar.start(failedIds.length, 0);
            this.progressActive = true;
        }

        const succeeded = [];
        const stillFailed = [];

        await Promise.all(failedIds.map(async amazonId => {
            const outcome = await this.getProductPageData(amazonId);
            if (outcome && outcome.success) {
                succeeded.push(amazonId);
            } else {
                stillFailed.push(amazonId);
            }
        }));

        if (this.progressBar && this.progressActive) {
            this.progressBar.stop();
            this.progressActive = false;
        }

        // Only rewrite the saved files when something was actually retried.
        if (failedIds.length > 0) {
            await this.checkAndSaveToFile();
        }

        const result = this.buildResult();
        const succeededSet = new Set(succeeded);
        result.retry = {
            attempted: failedIds.length,
            succeeded: failedIds.filter(amazonId => succeededSet.has(amazonId)),
            stillFailed: failedIds.filter(amazonId => !succeededSet.has(amazonId)),
        };
        result.summary.lastRetry = Object.assign({}, result.retry);

        console.log(`Retried detail data for ${result.retry.attempted} failed product(s): ${result.retry.succeeded.length} succeeded, ${result.retry.stillFailed.length} still failed.`);

        return result;
    }

    loadProducts(source) {
        let products = [];

        if (Array.isArray(source)) {
            products = source;

            // startScraping() returns the product array itself with non-enumerable metadata.
            if (source.summary) {
                this.loadSummary(source.summary);
            }
        } else if (source && Array.isArray(source.products)) {
            products = source.products;
            this.loadSummary(source.summary || {});
        }

        products.forEach(product => {
            if (product && product['amazon-id']) {
                this.alreadyScrappedProducts[product['amazon-id']] = product;
            }
        });
    }

    loadSummary(summary) {
        if (typeof summary.searchPageEmpty === 'boolean') {
            this.searchPageEmpty = summary.searchPageEmpty;
        }
        if (typeof summary.searchPageFailed === 'boolean') {
            this.searchPageFailed = summary.searchPageFailed;
        }
        if (summary.stopReason !== undefined) {
            this.stopReason = summary.stopReason;
        }
        if (typeof summary.searchPageFailureReason === 'string') {
            this.searchPageFailureReason = summary.searchPageFailureReason;
        }
        if (typeof summary.keyword === 'string' && summary.keyword) {
            this.keyword = summary.keyword;
        }
    }

    buildResult() {
        const products = Object.values(this.alreadyScrappedProducts);
        const failedProducts = products.filter(product => product[DETAIL_STATUS_FIELD] === DETAIL_STATUS.failed);
        const detailSuccessCount = products.filter(product => product[DETAIL_STATUS_FIELD] === DETAIL_STATUS.success).length;

        const summary = {
            keyword: this.keyword,
            requestedProducts: this.numberOfProducts,
            scrapedProducts: products.length,
            detailSuccessCount,
            detailFailedCount: failedProducts.length,
            hasDetailFailures: failedProducts.length > 0,
            failedAmazonIds: failedProducts.map(product => product['amazon-id']),
            searchPageEmpty: this.searchPageEmpty,
            searchPageFailed: this.searchPageFailed,
            stopReason: this.stopReason,
            searchPageFailureReason: this.searchPageFailureReason,
        };

        // The return value is still an array of products (backwards compatible with existing
        // callers), with the run-level outcome attached as non-enumerable metadata:
        //   result.products -> same array
        //   result.summary  -> counts, failure ids and why scraping stopped
        // so a successful detail enrichment, an exhausted retry and an empty search page
        // can all be told apart. Per-product outcomes are in the detail-status columns.
        return buildResultObject(products, summary);
    }

    describeError(error) {
        if (!error) {
            return 'unknown error';
        }
        if (typeof error === 'string') {
            return error;
        }
        return error.message || String(error);
    }
}

module.exports = ProductsScraper
