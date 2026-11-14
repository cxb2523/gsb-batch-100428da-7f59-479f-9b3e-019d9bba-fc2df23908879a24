'use strict';

const { test, describe, beforeEach } = require('node:test');
const assert = require('node:assert/strict');

const { loadContentModule } = require('./helpers');

describe('CSV export conventions', () => {
  let content;

  beforeEach(() => {
    content = loadContentModule();
  });

  test('buildCSVString keeps the existing header and row order', () => {
    const comments = [
      { username: 'budi', rating: 5, date: '2 hari lalu', comment: 'barang bagus' },
      { username: 'siti', rating: 1, date: 'kemarin', comment: 'kurang' }
    ];

    const csv = content.buildCSVString(comments);
    const lines = csv.split('\n');

    assert.equal(lines[0], 'Username,Rating,Date,Comment');
    assert.equal(lines[1], 'budi,5,2 hari lalu,barang bagus');
    assert.equal(lines[2], 'siti,1,kemarin,kurang');
  });

  test('quotes fields containing commas, quotes or new lines and escapes quotes', () => {
    const comments = [
      { username: 'a,b', rating: 3, date: 'd', comment: 'contains "quote"' },
      { username: 'c', rating: 4, date: 'd', comment: 'line1\nline2' }
    ];

    const csv = content.buildCSVString(comments);
    assert.ok(csv.includes('"a,b",3,d,"contains ""quote"""'));
    assert.ok(csv.includes('c,4,d,"line1\nline2"'));
  });

  test('downloadCSVChunk prefixes UTF-8 BOM and marks partial filenames', () => {
    // Minimal DOM stubs for the Blob/anchor download path
    const createdLinks = [];
    const createdBlobs = [];

    global.Blob = class FakeBlob {
      constructor(parts, options) {
        this.parts = parts;
        this.options = options;
        createdBlobs.push(this);
      }
    };
    global.URL = {
      createObjectURL: () => 'blob:fake-url',
      revokeObjectURL: () => {}
    };
    global.document = {
      createElement(tag) {
        assert.equal(tag, 'a');
        const link = {
          href: '',
          download: '',
          clickCalls: 0,
          click() {
            this.clickCalls++;
          }
        };
        createdLinks.push(link);
        return link;
      }
    };

    const fresh = loadContentModule();
    const comments = [{ username: 'budi', rating: 5, date: 'd', comment: 'cukup panjang' }];

    const fullName = fresh.downloadCSVChunk(comments, 1, 2, false);
    const partialName = fresh.downloadCSVChunk(comments, 1, 2, true);

    assert.match(fullName, /^shopee_comments_chunk_1_of_2_\d{4}-\d{2}-\d{2}\.csv$/);
    assert.match(partialName, /^shopee_comments_chunk_1_of_2_partial_\d{4}-\d{2}-\d{2}\.csv$/);
    assert.ok(!fullName.includes('_partial'));

    // BOM sits before the header in every download
    createdBlobs.forEach(blob => {
      assert.equal(blob.options.type, 'text/csv;charset=utf-8;');
      const payload = blob.parts.join('');
      assert.ok(payload.startsWith(fresh.UTF8_BOM), 'CSV must start with the UTF-8 BOM');
      assert.ok(payload.includes('Username,Rating,Date,Comment\n'));
    });
    assert.equal(createdLinks.length, 2);
    createdLinks.forEach(link => assert.equal(link.clickCalls, 1));

    delete global.Blob;
    delete global.URL;
    delete global.document;
  });
});

describe('checkpoint helpers', () => {
  test('chunkNumberOf maps pages to 100-page chunk numbers', () => {
    const content = loadContentModule();
    assert.equal(content.chunkNumberOf(1), 1);
    assert.equal(content.chunkNumberOf(100), 1);
    assert.equal(content.chunkNumberOf(101), 2);
    assert.equal(content.chunkNumberOf(250), 3);
  });

  test('createInitialCheckpoint describes a fresh, resumable state', () => {
    const content = loadContentModule();
    const cp = content.createInitialCheckpoint({
      productKey: 'p',
      productTitle: 'P',
      maxPages: 250,
      previousComments: 4,
      now: () => 'fixed-now'
    });

    assert.equal(cp.version, content.CHECKPOINT_VERSION);
    assert.equal(cp.productKey, 'p');
    assert.equal(cp.maxPages, 250);
    assert.equal(cp.currentChunk, 1);
    assert.equal(cp.nextPage, 1);
    assert.equal(cp.lastCompletedPage, 0);
    assert.equal(cp.totalComments, 4);
    assert.deepEqual(cp.exportedChunks, []);
    assert.equal(cp.endedEarly, false);
    assert.equal(cp.endReason, null);
    assert.equal(cp.completed, false);
    assert.equal(cp.createdAt, 'fixed-now');
  });

  test('loadCheckpoint returns null when nothing is stored', async () => {
    const content = loadContentModule();
    const storage = content.createInMemoryStorage();
    assert.equal(await content.loadCheckpoint(storage), null);
  });
});
