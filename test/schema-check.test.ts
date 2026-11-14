import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { parseProductDetail } from '../src/routes/detail-parser.js';
import { createRecordingLogger } from './helpers/recording-logger.js';
import { buildFullGraphqlCache, PRODUCT_URL } from './fixtures/graphql-product-cache.js';

type FieldSchema = {
    type: string | string[];
    properties?: Record<string, FieldSchema>;
    items?: FieldSchema;
};

const here = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(resolve(here, '../.actor/dataset_schema.json'), 'utf8'));
const { properties } = schema.fields as { properties: Record<string, FieldSchema> };

const validateNode = (value: unknown, propSchema: FieldSchema, path: string) => {
    const allowed = Array.isArray(propSchema.type) ? propSchema.type : [propSchema.type];
    if (value === null) {
        assert.ok(allowed.includes('null'), `${path} should allow null`);
        return;
    }
    if (allowed.includes('array')) {
        assert.ok(Array.isArray(value), `${path} should be an array`);
    }
    if (allowed.includes('object')) {
        assert.ok(typeof value === 'object' && !Array.isArray(value), `${path} should be an object`);
    }
    if (allowed.includes('string')) {
        assert.equal(typeof value, 'string');
    }
    if (allowed.includes('boolean')) {
        assert.equal(typeof value, 'boolean');
    }
    if (allowed.includes('number')) {
        assert.equal(typeof value, 'number');
    }

    if (Array.isArray(value) && propSchema.items?.properties) {
        for (const item of value) {
            validateNode(item, propSchema.items, `${path}[]`);
        }
    }
    if (typeof value === 'object' && value !== null && !Array.isArray(value) && propSchema.properties) {
        for (const [key, childValue] of Object.entries(value)) {
            validateNode(childValue, propSchema.properties[key], `${path}.${key}`);
        }
    }
};

describe('output contract against dataset_schema.json', () => {
    it('the fully parsed record conforms to every declared dataset field type', () => {
        const result = parseProductDetail(buildFullGraphqlCache(), PRODUCT_URL, createRecordingLogger());
        assert.ok(result.detail);
        const record = result.detail as Record<string, unknown>;

        // Every field declared in the dataset schema must be present and type-conformant.
        // (The record may additionally carry `comingSoon`, which the legacy route also emitted
        // but the schema does not declare; preserving it keeps output identical to before.)
        for (const key of Object.keys(properties)) {
            assert.ok(key in record, `declared dataset field ${key} is missing from the record`);
            validateNode(record[key], properties[key], key);
        }
    });
});
