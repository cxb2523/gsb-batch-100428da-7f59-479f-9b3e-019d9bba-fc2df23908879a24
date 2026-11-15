import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';

import { crawl } from '../dist/crawl.js';

function stubFetch(responses, requestedUrls) {
  return async (input) => {
    const requestedUrl = typeof input === 'string' ? input : input.url;
    requestedUrls?.push(requestedUrl);

    const response = responses.get(requestedUrl);
    if (response === undefined) {
      throw new Error(`Unexpected fetch for ${requestedUrl}`);
    }

    return {
      url: requestedUrl,
      status: response.status,
      headers: new Headers({
        'content-type': response.contentType,
      }),
      text: async () => response.body,
    };
  };
}

function baseOptions(outputDir, sitemap) {
  return {
    output: outputDir,
    singleFile: false,
    domain: false,
    maxPages: 10,
    concurrency: 1,
    delay: 0,
    sitemap,
    include: [],
    exclude: [],
    verbose: false,
  };
}

const SEED_PAGE = '<html><body><main><h1>Start</h1><p>Seed page with enough words for a successful extraction result.</p></main></body></html>';

test('crawl records per-sub-sitemap outcomes when one nested sitemap fails with HTTP 500', async () => {
  const originalFetch = global.fetch;
  const outputDir = path.join(process.cwd(), 'tmp-output', 'crawl-sitemap-partial-failure');

  const responses = new Map([
    [
      'https://docs.example.com/sitemap.xml',
      {
        status: 200,
        contentType: 'application/xml; charset=utf-8',
        body: '<?xml version="1.0"?><sitemapindex><sitemap><loc>https://docs.example.com/nested-broken.xml</loc></sitemap><sitemap><loc>https://docs.example.com/nested-ok.xml</loc></sitemap></sitemapindex>',
      },
    ],
    [
      'https://docs.example.com/nested-broken.xml',
      {
        status: 500,
        contentType: 'application/xml; charset=utf-8',
        body: 'Internal Server Error',
      },
    ],
    [
      'https://docs.example.com/nested-ok.xml',
      {
        status: 200,
        contentType: 'application/xml; charset=utf-8',
        body: '<?xml version="1.0"?><urlset><url><loc>https://docs.example.com/guide/intro</loc></url></urlset>',
      },
    ],
    [
      'https://docs.example.com/guide/start',
      {
        status: 200,
        contentType: 'text/html; charset=utf-8',
        body: SEED_PAGE,
      },
    ],
    [
      'https://docs.example.com/guide/intro',
      {
        status: 200,
        contentType: 'text/html; charset=utf-8',
        body: '<html><body><main><h1>Intro</h1><p>This page comes from the healthy nested sitemap and has enough words.</p></main></body></html>',
      },
    ],
  ]);

  global.fetch = stubFetch(responses);

  try {
    await fs.rm(outputDir, { recursive: true, force: true });

    const manifest = await crawl('https://docs.example.com/guide/start', baseOptions(outputDir, true));

    const introResult = manifest.results.find((result) => result.url === 'https://docs.example.com/guide/intro');
    assert.ok(introResult);
    assert.equal(introResult.status, 'success');
    assert.equal(manifest.summary.succeeded, 2);

    const brokenRecord = manifest.sitemapRecords.find((record) => record.url === 'https://docs.example.com/nested-broken.xml');
    assert.ok(brokenRecord);
    assert.equal(brokenRecord.status, 'failed');
    assert.match(brokenRecord.error ?? '', /HTTP 500/);
    assert.equal(brokenRecord.urlCount, 0);

    const okRecord = manifest.sitemapRecords.find((record) => record.url === 'https://docs.example.com/nested-ok.xml');
    assert.ok(okRecord);
    assert.equal(okRecord.status, 'success');
    assert.equal(okRecord.urlCount, 1);
  } finally {
    global.fetch = originalFetch;
    await fs.rm(outputDir, { recursive: true, force: true });
  }
});

test('crawl survives a nested sitemap with invalid XML and records the parse failure', async () => {
  const originalFetch = global.fetch;
  const outputDir = path.join(process.cwd(), 'tmp-output', 'crawl-sitemap-invalid-xml');

  const responses = new Map([
    [
      'https://docs.example.com/sitemap.xml',
      {
        status: 200,
        contentType: 'application/xml; charset=utf-8',
        body: '<?xml version="1.0"?><sitemapindex><sitemap><loc>https://docs.example.com/nested-bad.xml</loc></sitemap><sitemap><loc>https://docs.example.com/nested-good.xml</loc></sitemap></sitemapindex>',
      },
    ],
    [
      'https://docs.example.com/nested-bad.xml',
      {
        status: 200,
        contentType: 'application/xml; charset=utf-8',
        body: 'this is not <valid xml at all',
      },
    ],
    [
      'https://docs.example.com/nested-good.xml',
      {
        status: 200,
        contentType: 'application/xml; charset=utf-8',
        body: '<?xml version="1.0"?><urlset><url><loc>https://docs.example.com/guide/ok</loc></url></urlset>',
      },
    ],
    [
      'https://docs.example.com/guide/start',
      {
        status: 200,
        contentType: 'text/html; charset=utf-8',
        body: SEED_PAGE,
      },
    ],
    [
      'https://docs.example.com/guide/ok',
      {
        status: 200,
        contentType: 'text/html; charset=utf-8',
        body: '<html><body><main><h1>Ok</h1><p>This page comes from the sibling sitemap that parsed without any problems.</p></main></body></html>',
      },
    ],
  ]);

  global.fetch = stubFetch(responses);

  try {
    await fs.rm(outputDir, { recursive: true, force: true });

    const manifest = await crawl('https://docs.example.com/guide/start', baseOptions(outputDir, true));

    const okResult = manifest.results.find((result) => result.url === 'https://docs.example.com/guide/ok');
    assert.ok(okResult);
    assert.equal(okResult.status, 'success');

    const badRecord = manifest.sitemapRecords.find((record) => record.url === 'https://docs.example.com/nested-bad.xml');
    assert.ok(badRecord);
    assert.equal(badRecord.status, 'failed');
    assert.equal(typeof badRecord.error, 'string');
    assert.notEqual(badRecord.error, '');

    const goodRecord = manifest.sitemapRecords.find((record) => record.url === 'https://docs.example.com/nested-good.xml');
    assert.ok(goodRecord);
    assert.equal(goodRecord.status, 'success');
    assert.equal(goodRecord.urlCount, 1);
  } finally {
    global.fetch = originalFetch;
    await fs.rm(outputDir, { recursive: true, force: true });
  }
});

test('crawl records a duplicate nested sitemap reference only once and tolerates self-references', async () => {
  const originalFetch = global.fetch;
  const outputDir = path.join(process.cwd(), 'tmp-output', 'crawl-sitemap-duplicate-refs');

  const responses = new Map([
    [
      'https://docs.example.com/sitemap.xml',
      {
        status: 200,
        contentType: 'application/xml; charset=utf-8',
        body: '<?xml version="1.0"?><sitemapindex><sitemap><loc>https://docs.example.com/nested.xml</loc></sitemap><sitemap><loc>https://docs.example.com/nested.xml</loc></sitemap></sitemapindex>',
      },
    ],
    [
      'https://docs.example.com/nested.xml',
      {
        status: 200,
        contentType: 'application/xml; charset=utf-8',
        body: '<?xml version="1.0"?><sitemapindex><sitemap><loc>https://docs.example.com/nested.xml</loc></sitemap><sitemap><loc>https://docs.example.com/sitemap.xml</loc></sitemap></sitemapindex>',
      },
    ],
    [
      'https://docs.example.com/guide/start',
      {
        status: 200,
        contentType: 'text/html; charset=utf-8',
        body: SEED_PAGE,
      },
    ],
  ]);

  global.fetch = stubFetch(responses);

  try {
    await fs.rm(outputDir, { recursive: true, force: true });

    const manifest = await crawl('https://docs.example.com/guide/start', baseOptions(outputDir, true));

    const nestedRecords = manifest.sitemapRecords.filter((record) => record.url === 'https://docs.example.com/nested.xml');
    assert.equal(nestedRecords.length, 1);
    assert.equal(nestedRecords[0]?.status, 'success');
  } finally {
    global.fetch = originalFetch;
    await fs.rm(outputDir, { recursive: true, force: true });
  }
});

test('crawl produces no sitemap records and no sitemap requests when the sitemap option is disabled', async () => {
  const originalFetch = global.fetch;
  const outputDir = path.join(process.cwd(), 'tmp-output', 'crawl-sitemap-disabled');
  const requestedUrls = [];

  global.fetch = stubFetch(new Map([
    [
      'https://docs.example.com/guide/start',
      {
        status: 200,
        contentType: 'text/html; charset=utf-8',
        body: SEED_PAGE,
      },
    ],
  ]), requestedUrls);

  try {
    await fs.rm(outputDir, { recursive: true, force: true });

    const manifest = await crawl('https://docs.example.com/guide/start', baseOptions(outputDir, false));

    assert.deepEqual(manifest.sitemapRecords, []);
    assert.equal(requestedUrls.some((url) => url.includes('sitemap')), false);
  } finally {
    global.fetch = originalFetch;
    await fs.rm(outputDir, { recursive: true, force: true });
  }
});
