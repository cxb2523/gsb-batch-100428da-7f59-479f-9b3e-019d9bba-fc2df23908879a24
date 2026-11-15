'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const zlib = require('zlib');

const ProductsScraper = require('../products-scraper');
const CONSTANTS = require('../constants');
const utils = require('../utils');

const FIXTURES = path.join(__dirname, 'fixtures');
const SEARCH_PAGE = fs.readFileSync(path.join(FIXTURES, 'search-page.html'), 'utf8');
const EMPTY_SEARCH_PAGE = fs.readFileSync(path.join(FIXTURES, 'empty-search-page.html'), 'utf8');
const DETAIL_PAGE = fs.readFileSync(path.join(FIXTURES, 'detail-page.html'), 'utf8');

const OK_ASIN = 'B000OK0001';
const BAD_ASIN = 'B000BAD001';

// Short retry delays keep the tests fast; the number of retries itself always comes from constants.js.
const FAST_RETRY = { minTimeout: 1, maxTimeout: 1 };

const createScraper = (client, overrides = {}) => new ProductsScraper(Object.assign({
    keyword: 'baking mat',
    number: 2,
    host: 'amazon.test',
    client,
    retryOptions: FAST_RETRY,
}, overrides));

const createMockClient = handler => {
    const calls = [];
    return {
        calls,
        // Async on purpose: failures must reject, exactly like the real HTTP client does.
        async scrape(url, params) {
            calls.push({ url, params });
            return handler(url, params);
        }
    };
};

const okSearchThenDetail = (detailOverrides = {}) => createMockClient(url => {
    if (url.includes('/s?')) {
        // The fixture only holds the first search page; later pages are empty (end of results).
        return { content: /[?&]page=/.test(url) ? EMPTY_SEARCH_PAGE : SEARCH_PAGE };
    }
    if (url.includes(`/dp/${OK_ASIN}`)) {
        return { content: DETAIL_PAGE };
    }
    if (url.includes(`/dp/${BAD_ASIN}`)) {
        if (detailOverrides.bad) {
            return detailOverrides.bad(url);
        }
        throw new Error('detail page is unavailable');
    }
    throw new Error(`Unexpected URL: ${url}`);
});

const productById = (result, amazonId) => result.products.find(product => product['amazon-id'] === amazonId);

test('scraping reports success, retry-exhausted failure and the search-page-empty case separately', async () => {
    const client = okSearchThenDetail();
    const result = await createScraper(client).startScraping();

    // Both products from the search page are kept.
    assert.equal(result.products.length, 2);
    assert.equal(result.summary.stopReason, CONSTANTS.stopReasons.productLimitReached);
    assert.equal(result.summary.searchPageEmpty, false);

    const okProduct = productById(result, OK_ASIN);
    const failedProduct = productById(result, BAD_ASIN);

    // Success case.
    assert.equal(okProduct[CONSTANTS.productDetail.statusField], CONSTANTS.productDetail.statuses.success);
    assert.equal(okProduct[CONSTANTS.productDetail.reasonField], '');
    assert.match(okProduct['short-description'], /Short description from the detail page/);
    assert.match(okProduct['full-description'], /Full description from the detail page/);

    // Failure case: search-page fields survive, but it is explicitly marked as failed.
    assert.equal(failedProduct[CONSTANTS.productDetail.statusField], CONSTANTS.productDetail.statuses.failed);
    assert.match(failedProduct[CONSTANTS.productDetail.reasonField], /detail page is unavailable/);
    assert.equal(failedProduct.title, 'Flaky Baking Mat');
    assert.equal(failedProduct.price, '9.49');
    assert.equal(failedProduct['amazon-id'], BAD_ASIN);
    assert.equal(failedProduct['short-description'], '');
    assert.equal(failedProduct['full-description'], '');

    assert.equal(result.summary.detailSuccessCount, 1);
    assert.equal(result.summary.detailFailedCount, 1);
    assert.equal(result.summary.hasDetailFailures, true);
    assert.deepEqual(result.summary.failedAmazonIds, [BAD_ASIN]);

    // Retry count is not loosened: 3 outer attempts x (1 + 3 promise-retry attempts).
    const badDetailCalls = client.calls.filter(call => call.url.includes(`/dp/${BAD_ASIN}`));
    assert.equal(badDetailCalls.length, CONSTANTS.limit.retry * (1 + CONSTANTS.limit.retry));
});

test('a product whose detail page loads but has no descriptions is marked as failed', async () => {
    const client = okSearchThenDetail({
        bad: () => ({ content: '<html><body><div id="nav">nothing useful</div></body></html>' })
    });
    const result = await createScraper(client).startScraping();
    const failedProduct = productById(result, BAD_ASIN);

    assert.equal(failedProduct[CONSTANTS.productDetail.statusField], CONSTANTS.productDetail.statuses.failed);
    assert.match(failedProduct[CONSTANTS.productDetail.reasonField], /returned no short or full description/);
});

test('an empty search page is reported as such instead of a silent empty result', async () => {
    const client = createMockClient(() => ({ content: EMPTY_SEARCH_PAGE }));
    const result = await createScraper(client).startScraping();

    assert.deepEqual(result.products, []);
    assert.equal(result.summary.searchPageEmpty, true);
    assert.equal(result.summary.stopReason, CONSTANTS.stopReasons.searchPageNoResults);
    assert.match(result.summary.searchPageFailureReason, /returned no products/);

    // The search page is only fetched the configured number of attempts, never more.
    assert.equal(client.calls.length, CONSTANTS.limit.retry);
});

test('a search page that keeps erroring is reported separately from an empty one', async () => {
    const client = createMockClient(() => {
        throw new Error('search endpoint is unreachable');
    });
    const result = await createScraper(client).startScraping();

    assert.deepEqual(result.products, []);
    assert.equal(result.summary.searchPageEmpty, true);
    assert.equal(result.summary.searchPageFailed, true);
    assert.equal(result.summary.stopReason, CONSTANTS.stopReasons.searchPageFailed);
    assert.match(result.summary.searchPageFailureReason, /search endpoint is unreachable/);
    assert.equal(client.calls.length, CONSTANTS.limit.retry * (1 + CONSTANTS.limit.retry));
});

test('the result stays a plain product array for existing callers', async () => {
    const result = await createScraper(okSearchThenDetail()).startScraping();

    assert.ok(Array.isArray(result));
    assert.equal(result.length, 2);

    // Run metadata is non-enumerable, so JSON output and for-of iteration stay products-only.
    const serialised = JSON.parse(JSON.stringify(result));
    assert.equal(serialised.length, 2);
    assert.ok(!('summary' in serialised[0]));
    assert.equal([...result].length, 2);
});

test('retryFailedProducts retries only failed products and clears the marker on success', async () => {
    const failingClient = okSearchThenDetail();
    const scraper = createScraper(failingClient);
    const firstRun = await scraper.startScraping();

    assert.equal(firstRun.summary.detailFailedCount, 1);

    // Swap the client for one where the detail page works again.
    scraper.client = okSearchThenDetail({ bad: () => ({ content: DETAIL_PAGE }) });

    const retried = await scraper.retryFailedProducts();
    const retriedProduct = productById(retried, BAD_ASIN);

    assert.deepEqual(retried.retry.succeeded, [BAD_ASIN]);
    assert.deepEqual(retried.retry.stillFailed, []);
    assert.equal(retried.retry.attempted, 1);

    assert.equal(retriedProduct[CONSTANTS.productDetail.statusField], CONSTANTS.productDetail.statuses.success);
    assert.equal(retriedProduct[CONSTANTS.productDetail.reasonField], '');
    assert.match(retriedProduct['short-description'], /Short description from the detail page/);

    // No duplicate rows, and the successful product was not fetched again.
    assert.equal(retried.products.length, 2);
    const detailCalls = scraper.client.calls.filter(call => call.url.includes('/dp/'));
    assert.deepEqual(detailCalls.map(call => call.url), [`https://amazon.test/dp/${BAD_ASIN}`]);
});

test('a repeated failure updates the marker and reason without accumulating entries', async () => {
    let attempt = 0;
    const client = okSearchThenDetail({
        bad: () => {
            attempt++;
            throw new Error(`detail page is unavailable (attempt ${attempt})`);
        }
    });
    const scraper = createScraper(client);
    await scraper.startScraping();

    const retried = await scraper.retryFailedProducts();
    const retriedProduct = productById(retried, BAD_ASIN);

    assert.deepEqual(retried.retry.stillFailed, [BAD_ASIN]);
    assert.equal(retried.products.length, 2);
    assert.equal(retriedProduct[CONSTANTS.productDetail.statusField], CONSTANTS.productDetail.statuses.failed);

    // The reason reflects the latest attempt only - not an accumulation of the first run's reason.
    const callsPerRun = CONSTANTS.limit.retry * (1 + CONSTANTS.limit.retry);
    const reason = retriedProduct[CONSTANTS.productDetail.reasonField];
    assert.match(reason, new RegExp(`attempt ${callsPerRun * 2}\\)`));
    assert.equal(reason.match(/detail page is unavailable/g).length, 1);
});

test('retryFailedProducts can resume from a previously saved result object', async () => {
    const scraper = createScraper(okSearchThenDetail());
    const firstRun = await scraper.startScraping();

    // Simulate loading a result saved earlier, with a now-working detail page.
    const resumed = createScraper(okSearchThenDetail({ bad: () => ({ content: DETAIL_PAGE }) }));
    const result = await resumed.retryFailedProducts(JSON.parse(JSON.stringify(firstRun)));

    assert.deepEqual(result.retry.succeeded, [BAD_ASIN]);
    assert.equal(result.products.length, 2);
    assert.equal(productById(result, OK_ASIN)[CONSTANTS.productDetail.statusField], CONSTANTS.productDetail.statuses.success);
});

test('retryFailedProducts can resume from a saved state object with summary metadata', async () => {
    const firstRun = await createScraper(okSearchThenDetail()).startScraping();
    const state = JSON.parse(JSON.stringify({
        keyword: firstRun.summary.keyword,
        summary: firstRun.summary,
        products: firstRun.products,
    }));

    const resumedClient = okSearchThenDetail({ bad: () => ({ content: DETAIL_PAGE }) });
    const result = await createScraper(resumedClient).retryFailedProducts(state);

    assert.deepEqual(result.retry.succeeded, [BAD_ASIN]);
    assert.equal(result.summary.keyword, 'baking mat');
    assert.equal(result.summary.detailFailedCount, 0);
    assert.equal(result.summary.searchPageEmpty, false);

    // Only the failed product is fetched, using the unchanged retry budget.
    assert.equal(resumedClient.calls.length, 1);
    assert.equal(resumedClient.calls[0].url, `https://amazon.test/dp/${BAD_ASIN}`);
});

test('the retry budget can not be loosened through options', async () => {
    const client = okSearchThenDetail();
    const scraper = createScraper(client, { retryOptions: { retries: 99, minTimeout: 1, maxTimeout: 1 } });
    await scraper.startScraping();

    const badDetailCalls = client.calls.filter(call => call.url.includes(`/dp/${BAD_ASIN}`));
    assert.equal(badDetailCalls.length, CONSTANTS.limit.retry * (1 + CONSTANTS.limit.retry));
});

test('CSV keeps the original column order and appends the detail columns at the end', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'scraper-csv-'));
    const previousCwd = process.cwd();
    process.chdir(directory);

    try {
        // Saved through the scraper itself, so the original file naming rule is exercised too.
        const result = await createScraper(okSearchThenDetail(), { save: true, fileType: 'csv' }).startScraping();

        const files = fs.readdirSync(directory).filter(name => name.endsWith('.csv'));
        assert.equal(files.length, 1);
        assert.match(files[0], /^baking_mat_product_\d+\.csv$/);

        const rows = parseCsv(fs.readFileSync(path.join(directory, files[0]), 'utf8'));
        const header = rows[0];

        // Existing columns keep their historical order; the two detail columns are appended.
        assert.deepEqual(header, [
            'title', 'price', 'savings', 'rating', 'reviews-count', 'score', 'url', 'is-sponsored',
            'is-amazon-choice', 'is-discounted', 'before-discount', 'amazon-id', 'thumbnail',
            'high-res-image', 'short-description', 'full-description',
            CONSTANTS.productDetail.statusField, CONSTANTS.productDetail.reasonField
        ]);

        assert.equal(rows.length, 3);
        const failedRow = rows.find(row => row[header.indexOf('amazon-id')] === BAD_ASIN);
        assert.equal(failedRow[header.indexOf(CONSTANTS.productDetail.statusField)], CONSTANTS.productDetail.statuses.failed);
        assert.match(failedRow[header.indexOf(CONSTANTS.productDetail.reasonField)], /detail page is unavailable/);
        // Search page fields are preserved on the failed row.
        assert.equal(failedRow[header.indexOf('title')], 'Flaky Baking Mat');
        assert.equal(failedRow[header.indexOf('price')], '9.49');

        const okRow = rows.find(row => row[header.indexOf('amazon-id')] === OK_ASIN);
        assert.equal(okRow[header.indexOf(CONSTANTS.productDetail.statusField)], CONSTANTS.productDetail.statuses.success);
        assert.equal(okRow[header.indexOf(CONSTANTS.productDetail.reasonField)], '');
    } finally {
        process.chdir(previousCwd);
    }
});

test('XLS keeps the original column order and appends the detail columns at the end', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'scraper-xls-'));
    const previousCwd = process.cwd();
    process.chdir(directory);

    try {
        const result = await createScraper(okSearchThenDetail(), { save: true, fileType: 'xls' }).startScraping();

        const files = fs.readdirSync(directory).filter(name => name.endsWith('.xls'));
        assert.equal(files.length, 1);
        assert.match(files[0], /^baking_mat_product_\d+\.xls$/);

        const workbook = readZipEntry(path.join(directory, files[0]), 'xl/sharedStrings.xml');
        const strings = [...workbook.matchAll(/<x:si><x:t>(.*?)<\/x:t><\/x:si>/gs)].map(match => match[1]);
        const header = strings.slice(0, utils.XLS_FIELDS.length);

        assert.deepEqual(header, [
            'amazon-id', 'title', 'thumbnail', 'high-res-image', 'url', 'is-discounted', 'is-sponsored',
            'is-amazon-choice', 'price', 'before-discount', 'reviews-count', 'rating', 'score', 'savings',
            'short-description', 'full-description', 'detail-status', 'detail-failure-reason'
        ]);

        // The failure marker is actually present in the sheet's shared strings.
        assert.ok(strings.includes(CONSTANTS.productDetail.statuses.failed));

        const sheet = readZipEntry(path.join(directory, files[0]), 'xl/worksheets/sheet.xml');
        const firstRow = sheet.match(/<x:row r="1"[^>]*>(.*?)<\/x:row>/)[1];
        assert.equal((firstRow.match(/<x:c /g) || []).length, utils.XLS_FIELDS.length);
    } finally {
        process.chdir(previousCwd);
    }
});

// Minimal ZIP reader for the .xls files produced by json2xls (deflated entries only).
function readZipEntry(filePath, entryName) {
    const buffer = fs.readFileSync(filePath);
    const signature = Buffer.from([0x50, 0x4b, 0x03, 0x04]);
    let offset = 0;

    while ((offset = buffer.indexOf(signature, offset)) !== -1) {
        const method = buffer.readUInt16LE(offset + 8);
        const compressedSize = buffer.readUInt32LE(offset + 18);
        const nameLength = buffer.readUInt16LE(offset + 26);
        const extraLength = buffer.readUInt16LE(offset + 28);
        const name = buffer.toString('utf8', offset + 30, offset + 30 + nameLength);
        const dataStart = offset + 30 + nameLength + extraLength;

        if (name === entryName) {
            const data = buffer.slice(dataStart, dataStart + compressedSize);
            return (method === 8 ? zlib.inflateRawSync(data) : data).toString('utf8');
        }

        offset = dataStart + compressedSize;
    }

    throw new Error(`Entry ${entryName} not found in ${filePath}`);
}

// Minimal RFC4180 CSV parser (quoted fields, escaped quotes, newlines inside quotes).
function parseCsv(text) {
    const rows = [];
    let row = [];
    let field = '';
    let quoted = false;

    for (let i = 0; i < text.length; i++) {
        const character = text[i];

        if (quoted) {
            if (character === '"') {
                if (text[i + 1] === '"') {
                    field += '"';
                    i++;
                } else {
                    quoted = false;
                }
            } else {
                field += character;
            }
            continue;
        }

        if (character === '"') {
            quoted = true;
        } else if (character === ',') {
            row.push(field);
            field = '';
        } else if (character === '\n') {
            row.push(field);
            rows.push(row);
            row = [];
            field = '';
        } else if (character !== '\r') {
            field += character;
        }
    }

    if (field !== '' || row.length > 0) {
        row.push(field);
        rows.push(row);
    }

    return rows;
}
