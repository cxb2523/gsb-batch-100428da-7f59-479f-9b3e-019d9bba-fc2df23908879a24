'use strict';

const fs = require('fs').promises;
const Json2csvParser = require('json2csv').Parser;
const json2xls = require('json2xls');

const CONSTANTS = require('./constants');

// CSV columns keep their historical order. New columns are only appended at the end,
// so existing downstream parsers that read by position are not broken.
const CSV_BASE_FIELDS = ['title', 'price', 'savings', 'rating', 'reviews-count', 'score', 'url', 'is-sponsored', 'is-amazon-choice', 'is-discounted', 'before-discount', 'amazon-id', 'thumbnail', 'high-res-image', 'short-description', 'full-description'];

// XLS columns are derived from the product object key order, which historically started
// with the Amazon ID. Keep that order and append the new detail columns at the end.
const XLS_BASE_FIELDS = ['amazon-id', 'title', 'thumbnail', 'high-res-image', 'url', 'is-discounted', 'is-sponsored', 'is-amazon-choice', 'price', 'before-discount', 'reviews-count', 'rating', 'score', 'savings', 'short-description', 'full-description'];

const DETAIL_FIELDS = [CONSTANTS.productDetail.statusField, CONSTANTS.productDetail.reasonField];

const CSV_FIELDS = CSV_BASE_FIELDS.concat(DETAIL_FIELDS);
const XLS_FIELDS = XLS_BASE_FIELDS.concat(DETAIL_FIELDS);

// Exposed so callers and tests can verify the exact column order written to disk.
exports.CSV_FIELDS = CSV_FIELDS;
exports.XLS_FIELDS = XLS_FIELDS;

exports.writeDataToCsv = async (keyword, productsList) => {
    const productsParser = new Json2csvParser({
        fields: CSV_FIELDS,
    });

    return fs.writeFile(`${keyword}_product_${Date.now()}.csv`, productsParser.parse(productsList));
}

exports.writeDataToXls = async (keyword, productsList) =>
     fs.writeFile(`${keyword}_product_${Date.now()}.xls`, json2xls(productsList, { fields: XLS_FIELDS }), 'binary');
