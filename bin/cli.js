#!/usr/bin/env node
'use strict';

const fs = require('fs');
const ProductsScraper = require('../products-scraper');

const loadRetrySource = filePath => {
    const parsed = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    if (Array.isArray(parsed)) {
        return { products: parsed, summary: parsed.summary || null };
    }
    if (parsed && Array.isArray(parsed.products)) {
        return { products: parsed.products, summary: parsed.summary || null };
    }
    throw `Retry source file should contain either an array of products or a startScraping() result with a "products" array.`;
};

// Persists the run so failed products can be retried later. Kept separate from the CSV/XLS
// output so that those files keep their existing format.
const saveState = (filePath, result) => {
    fs.writeFileSync(filePath, JSON.stringify({
        keyword: result.summary.keyword,
        summary: result.summary,
        products: result.products,
    }, null, 2));
    console.log(`Scraping state saved to ${filePath}`);
};

const startScraper = async argv => {
    try {
        let keyword = argv.keyword;
        let source = null;

        if (argv.retryFailedFrom) {
            // Retry details only for products previously marked as failed.
            const loaded = loadRetrySource(argv.retryFailedFrom);
            source = loaded.summary ? { products: loaded.products, summary: loaded.summary } : loaded.products;
            keyword = keyword || (loaded.summary && loaded.summary.keyword);
        }

        const scraper = new ProductsScraper({ ...argv, keyword, showProgress: true });

        let result;
        if (source) {
            result = await scraper.retryFailedProducts(source);
            if (result.retry.attempted === 0) {
                console.log('No failed products to retry.');
            }
        } else {
            result = await scraper.startScraping();

            if (argv.retryFailed) {
                result = await scraper.retryFailedProducts();
            } else if (result.summary.hasDetailFailures) {
                console.log('Some products failed to get detail data. Re-run with --retryFailed on the same run or --retryFailedFrom <saved state json> to retry only those products.');
            }
        }

        if (argv.saveState) {
            saveState(argv.saveState, result);
        }
    } catch (error) {
        console.log(error);
    }
};

require('yargs')
    .usage('Usage: $0 [options]')
    .example(`$0 -s 'baking mat' -k 'ScrapingAnt API key'`)
    .example(`$0 -s 'baking mat' -H 'www.amazon.de' -k 'ScrapingAnt API key'`)
    .example(`$0 -s 'baking mat' -c 'us' -H 'www.amazon.de' -k 'ScrapingAnt API key'`)
    .example(`$0 -r -s 'baking mat' -a 'ScrapingAnt API key'`)
    .example(`$0 --retryFailedFrom ./result.json -a 'ScrapingAnt API key'`)
    .command('$0', 'Scrapes for a products with rotating proxies from the provided key word', {}, argv => {
        startScraper(argv);
    })
    .options({
        help: {
            alias: 'h',
            describe: 'help',
        },
        keyword: {
            alias: 'k',
            default: '',
            type: 'string',
            describe: "Amazon search keyword ex. 'baking mat'",
        },
        apiKey: {
            alias: 'a',
            default: '',
            type: 'string',
            describe: 'ScrapingAnt API key'
        },
        number: {
            alias: 'n',
            default: 10,
            type: 'number',
            describe: 'Number of products to scrape. Maximum 500 products',
        },
        save: {
            alias: 's',
            default: true,
            type: 'boolean',
            describe: 'Save to a file?',
        },
        fileType: {
            alias: 't',
            default: 'csv',
            describe: "File type to save: can be either csv or xls."
        },
        host: {
            alias: 'H',
            default: 'amazon.com',
            type: 'string',
            describe: 'The regional Amazon host (can be amazon.fr, amazon.co.uk, etc.)',
        },
        country: {
            alias: 'c',
            default: '',
            type: 'string',
            describe: 'Country of proxies location (default: all possible countries)'
        },
        retryFailed: {
            alias: 'r',
            default: false,
            type: 'boolean',
            describe: 'After scraping, retry detail pages only for products that failed.'
        },
        retryFailedFrom: {
            default: '',
            type: 'string',
            describe: 'Retry detail pages only for failed products from a previously saved --saveState JSON.'
        },
        saveState: {
            default: '',
            type: 'string',
            describe: 'Save the run result (products + summary) as JSON, so failed products can be retried later.'
        }
    })
    .check(argv => {
        if (!argv.apiKey) {
            throw 'ScrapingAnt API key is missing';
        }
        if (!argv.keyword && !argv.retryFailedFrom) {
            throw 'Search keyword is missing';
        }
        return true;
    })
    .demandCommand().argv;
