module.exports = {
    limit: {
        retry: 3,
        product: 500,
    },
    supported_countries: [
        'ae', // United Arab Emirates (the)
        'br', // Brasilia
        'cn', // China
        'de', // Germany
        'es', // Spain
        'fr', // France
        'gb', // United Kingdom (the)
        'hk', // Hong Kong
        'in', // India
        'it', // Italy
        'il', // Israel
        'jp', // Japan
        'nl', // Netherlands (the)
        'ru', // Russia
        'sa', // Saudi Arabia
        'us', // USA
    ],
    supported_filetypes: {
        xls: 'xls',
        csv: 'csv'
    },
    defaultItemLimit: 10,
    defaultAmazonUrl: 'amazon.com',
    productDetail: {
        // Values of the detail-status column. `pending` only exists while a product is being enriched.
        statuses: {
            pending: 'pending',
            success: 'success',
            failed: 'failed'
        },
        // New columns are appended at the end of the saved files, never inserted between existing ones.
        statusField: 'detail-status',
        reasonField: 'detail-failure-reason'
    },
    stopReasons: {
        productLimitReached: 'product-limit-reached',
        searchPageNoResults: 'search-page-no-results',
        searchPageFailed: 'search-page-failed',
        searchResultsExhausted: 'search-results-exhausted'
    }
};