'use strict';

const { test, describe, beforeEach } = require('node:test');
const assert = require('node:assert/strict');

const {
  loadContentModule,
  makeComments,
  createFakePage,
  createDownloadRecorder,
  createTestDeps,
  createChromeStorageStub
} = require('./helpers');

describe('runScraping: chunked, resumable scraping engine', () => {
  let content;

  beforeEach(() => {
    content = loadContentModule();
  });

  test('records a correct checkpoint after one chunk completes normally', async () => {
    const { runScraping, createInMemoryStorage, END_REASON, CHUNK_SIZE } = content;
    assert.equal(CHUNK_SIZE, 100);

    // One full chunk: 3 pages, 2 comments each
    const pages = [makeComments(1, 2), makeComments(2, 2), makeComments(3, 2)];
    const fakePage = createFakePage(pages);
    const storage = createInMemoryStorage();
    const recorder = createDownloadRecorder();
    const abortSignal = { stopped: false };

    const result = await runScraping(
      { maxPages: 3, productKey: 'p-1', productTitle: 'Produk Uji', resume: false },
      createTestDeps(content, { storage, fakePage, abortSignal, recorder })
    );

    assert.equal(result.endReason, END_REASON.COMPLETED);

    // Exactly one CSV export for the chunk
    assert.equal(recorder.downloads.length, 1);
    const download = recorder.downloads[0];
    assert.equal(download.chunkNumber, 1);
    assert.equal(download.partial, false);
    assert.equal(download.comments.length, 6);

    // Checkpoint persisted in storage
    const stored = await storage.get([content.CHECKPOINT_KEY]);
    const cp = stored[content.CHECKPOINT_KEY];
    assert.ok(cp, 'checkpoint must be stored under scrapeCheckpoint');
    assert.equal(cp.currentChunk, 1);
    assert.equal(cp.nextPage, 4);
    assert.equal(cp.lastCompletedPage, 3);
    assert.equal(cp.totalComments, 6);
    assert.equal(cp.endedEarly, false);
    assert.equal(cp.endReason, END_REASON.COMPLETED);
    assert.equal(cp.completed, true);

    // Exported chunk record carries number, count and page range
    assert.equal(cp.exportedChunks.length, 1);
    assert.deepEqual(
      {
        chunkNumber: cp.exportedChunks[0].chunkNumber,
        commentCount: cp.exportedChunks[0].commentCount,
        startPage: cp.exportedChunks[0].startPage,
        endPage: cp.exportedChunks[0].endPage,
        partial: cp.exportedChunks[0].partial
      },
      { chunkNumber: 1, commentCount: 6, startPage: 1, endPage: 3, partial: false }
    );
  });

  test('pagination failure still exports the current chunk with the failure reason', async () => {
    const { runScraping, createInMemoryStorage, END_REASON } = content;

    // 2 real pages; the attempt to reach page 3 has no working next button
    const pages = [makeComments(1, 3), makeComments(2, 3)];
    const fakePage = createFakePage(pages, { failAfterPage: 2 });
    const storage = createInMemoryStorage();
    const recorder = createDownloadRecorder();
    const abortSignal = { stopped: false };

    const result = await runScraping(
      { maxPages: 5, productKey: 'p-2', productTitle: 'Produk Uji', resume: false },
      createTestDeps(content, { storage, fakePage, abortSignal, recorder })
    );

    assert.equal(result.endReason, END_REASON.PAGINATION_FAILED);

    // The comments already collected are not lost: one partial CSV export
    assert.equal(recorder.downloads.length, 1);
    const download = recorder.downloads[0];
    assert.equal(download.partial, true);
    assert.equal(download.comments.length, 6);
    assert.deepEqual(
      download.comments.map(c => c.username),
      [...makeComments(1, 3).map(c => c.username), ...makeComments(2, 3).map(c => c.username)]
    );

    const cp = result.checkpoint;
    assert.equal(cp.endedEarly, true);
    assert.equal(cp.endReason, END_REASON.PAGINATION_FAILED);
    assert.equal(cp.completed, false);
    assert.equal(cp.lastCompletedPage, 2);
    assert.equal(cp.nextPage, 3);
    assert.equal(cp.totalComments, 6);
    assert.equal(cp.exportedChunks.length, 1);
    assert.deepEqual(
      {
        chunkNumber: cp.exportedChunks[0].chunkNumber,
        commentCount: cp.exportedChunks[0].commentCount,
        startPage: cp.exportedChunks[0].startPage,
        endPage: cp.exportedChunks[0].endPage,
        partial: cp.exportedChunks[0].partial
      },
      { chunkNumber: 1, commentCount: 6, startPage: 1, endPage: 2, partial: true }
    );

    // The unfinished chunk must not be recorded as a completed chunk
    assert.equal(cp.exportedChunks[0].partial, true);
  });

  test('pagination failure is also detected when the comment count stops growing', async () => {
    const { runScraping, createInMemoryStorage, END_REASON } = content;

    // Clicking through to page 4 succeeds mechanically but no new comments
    // appear (page 4 in the fake is empty).
    const pages = [makeComments(1, 2), makeComments(2, 2), makeComments(3, 2), []];
    const fakePage = createFakePage(pages);
    const storage = createInMemoryStorage();
    const recorder = createDownloadRecorder();
    const abortSignal = { stopped: false };

    const result = await runScraping(
      { maxPages: 4, productKey: 'p-2b', productTitle: 'Produk Uji', resume: false },
      createTestDeps(content, { storage, fakePage, abortSignal, recorder })
    );

    assert.equal(result.endReason, END_REASON.PAGINATION_FAILED);
    assert.equal(result.pagesLoaded, 3);
    assert.equal(recorder.downloads.length, 1);
    assert.equal(recorder.downloads[0].partial, true);
    assert.equal(recorder.downloads[0].comments.length, 6);
  });

  test('user stop is resumable and completed chunks are never re-exported', async () => {
    const { runScraping, createInMemoryStorage, END_REASON, CHUNK_SIZE } = content;
    assert.ok(CHUNK_SIZE >= 2, 'fixture needs a chunk size of at least 2 pages');

    const maxPages = CHUNK_SIZE * 2; // spans two chunks
    const pages = [];
    for (let page = 1; page <= maxPages; page++) {
      pages.push(makeComments(page, 2));
    }

    const productKey = 'p-3';
    const sharedStorage = createInMemoryStorage();
    const recorder = createDownloadRecorder();

    // ---- Run 1: user stops after the first 5 pages are collected ----
    let extractedPagesRun1 = 0;
    const abortSignal = { stopped: false };
    const fakePage1 = createFakePage(pages);
    const deps1 = createTestDeps(content, {
      storage: sharedStorage,
      fakePage: fakePage1,
      abortSignal,
      recorder
    });
    const originalExtract1 = deps1.extractPage;
    deps1.extractPage = async () => {
      const comments = await originalExtract1();
      extractedPagesRun1++;
      if (extractedPagesRun1 === 5) {
        abortSignal.stopped = true; // user presses stop mid-chunk
      }
      return comments;
    };

    const result1 = await runScraping(
      { maxPages, productKey, productTitle: 'Produk Uji', resume: false },
      deps1
    );

    assert.equal(result1.endReason, END_REASON.USER_STOPPED);
    assert.equal(result1.pagesLoaded, 5);
    assert.equal(result1.checkpoint.nextPage, 6);
    assert.equal(result1.checkpoint.completed, false);
    assert.equal(recorder.downloads.length, 1);
    assert.equal(recorder.downloads[0].partial, true);
    assert.equal(recorder.downloads[0].comments.length, 10);

    // ---- Run 2: resume from the recorded checkpoint ----
    abortSignal.stopped = false;
    const fakePage2 = createFakePage(pages);
    const deps2 = createTestDeps(content, {
      storage: sharedStorage,
      fakePage: fakePage2,
      abortSignal,
      recorder
    });

    const result2 = await runScraping(
      { maxPages, productKey, productTitle: 'Produk Uji', resume: true },
      deps2
    );

    assert.equal(result2.resumed, true);
    assert.equal(result2.endReason, END_REASON.COMPLETED);

    // Fast-forward did not re-scrape pages 1-5
    assert.deepEqual(
      fakePage2.advanceLog.slice(0, 5),
      [2, 3, 4, 5, 6]
    );
    assert.ok(!fakePage2.advanceLog.includes(1), 'page 1 must never be re-requested on resume');

    const cp = result2.checkpoint;
    assert.equal(cp.nextPage, maxPages + 1);
    assert.equal(cp.lastCompletedPage, maxPages);
    assert.equal(cp.completed, true);
    assert.equal(cp.endedEarly, false);
    assert.equal(cp.totalComments, maxPages * 2);

    // Exports: partial chunk-1 continuation + full chunk 2; nothing exported twice
    const chunk1Exports = cp.exportedChunks.filter(e => e.chunkNumber === 1);
    const chunk2Exports = cp.exportedChunks.filter(e => e.chunkNumber === 2);
    assert.equal(chunk1Exports.length, 2);
    assert.equal(chunk1Exports[0].startPage, 1);
    assert.equal(chunk1Exports[0].endPage, 5);
    assert.equal(chunk1Exports[0].partial, true);
    assert.equal(chunk1Exports[1].startPage, 6);
    assert.equal(chunk1Exports[1].endPage, CHUNK_SIZE);
    assert.equal(chunk1Exports[1].partial, false);
    assert.equal(chunk2Exports.length, 1);
    assert.equal(chunk2Exports[0].partial, false);
    assert.equal(chunk2Exports[0].startPage, CHUNK_SIZE + 1);
    assert.equal(chunk2Exports[0].endPage, maxPages);

    // No comment is exported twice across the whole session
    const allExportedUsernames = recorder.downloads.flatMap(d =>
      d.comments.map(c => c.username)
    );
    assert.equal(allExportedUsernames.length, new Set(allExportedUsernames).size);
    assert.equal(allExportedUsernames.length, maxPages * 2);

    // Total downloads = 1 (run 1) + 1 (continuation) + 1 (chunk 2)
    assert.equal(recorder.downloads.length, 3);
  });

  test('stop before the first page finishes exports nothing but stays resumable', async () => {
    const { runScraping, createInMemoryStorage, loadCheckpoint, END_REASON } = content;

    const pages = [makeComments(1, 2), makeComments(2, 2)];
    const storage = createInMemoryStorage();
    const recorder = createDownloadRecorder();
    const abortSignal = { stopped: true }; // user already pressed stop

    const result = await runScraping(
      { maxPages: 2, productKey: 'p-5', productTitle: 'Produk Uji', resume: false },
      createTestDeps(content, { storage, fakePage: createFakePage(pages), abortSignal, recorder })
    );

    assert.equal(result.endReason, END_REASON.USER_STOPPED);
    assert.equal(recorder.downloads.length, 0, 'no partial chunk may be invented when nothing was collected');
    assert.equal(result.pagesLoaded, 0);

    // The stored checkpoint is readable by a later run and points at page 1
    const stored = await loadCheckpoint(storage);
    assert.equal(stored.completed, false);
    assert.equal(stored.endedEarly, true);
    assert.equal(stored.endReason, END_REASON.USER_STOPPED);
    assert.equal(stored.nextPage, 1);
    assert.deepEqual(stored.exportedChunks, []);

    // A resumed run then completes normally
    abortSignal.stopped = false;
    const resumed = await runScraping(
      { maxPages: 2, productKey: 'p-5', productTitle: 'Produk Uji', resume: true },
      createTestDeps(content, { storage, fakePage: createFakePage(pages), abortSignal, recorder })
    );
    assert.equal(resumed.resumed, true);
    assert.equal(resumed.endReason, END_REASON.COMPLETED);
    assert.equal(recorder.downloads.length, 1);
    assert.equal(recorder.downloads[0].comments.length, 4);
  });

  test('exports every full chunk with the right numbering across a chunk boundary', async () => {
    const { runScraping, createInMemoryStorage, END_REASON, CHUNK_SIZE } = content;

    const maxPages = CHUNK_SIZE + 2;
    const pages = Array.from({ length: maxPages }, (_, idx) => makeComments(idx + 1, 1));
    const storage = createInMemoryStorage();
    const recorder = createDownloadRecorder();
    const abortSignal = { stopped: false };

    const result = await runScraping(
      { maxPages, productKey: 'p-6', productTitle: 'Produk Uji', resume: false },
      createTestDeps(content, { storage, fakePage: createFakePage(pages), abortSignal, recorder })
    );

    assert.equal(result.endReason, END_REASON.COMPLETED);
    assert.equal(recorder.downloads.length, 2);
    assert.deepEqual(
      recorder.downloads.map(d => [d.chunkNumber, d.totalChunks, d.partial]),
      [[1, 2, false], [2, 2, false]]
    );
    assert.deepEqual(result.checkpoint.exportedChunks.map(e => e.commentCount), [CHUNK_SIZE, 2]);
    assert.deepEqual(
      result.checkpoint.exportedChunks.map(e => [e.startPage, e.endPage]),
      [[1, CHUNK_SIZE], [CHUNK_SIZE + 1, maxPages]]
    );
  });

  test('a completed checkpoint makes the next run start fresh from page 1', async () => {
    const { runScraping, createInMemoryStorage, END_REASON } = content;

    const pages = [makeComments(1, 1), makeComments(2, 1)];
    const storage = createInMemoryStorage();
    const recorder = createDownloadRecorder();
    const abortSignal = { stopped: false };
    const options = { maxPages: 2, productKey: 'p-4', productTitle: 'Produk Uji', resume: true };
    const makeDeps = fakePage => createTestDeps(content, {
      storage, fakePage, abortSignal, recorder
    });

    const first = await runScraping(options, makeDeps(createFakePage(pages)));
    assert.equal(first.endReason, END_REASON.COMPLETED);
    assert.equal(first.checkpoint.completed, true);

    const second = await runScraping(options, makeDeps(createFakePage(pages)));
    assert.equal(second.resumed, false, 'completed checkpoints must not be resumed');
    assert.equal(second.pagesLoaded, 2);
  });

  test('a checkpoint for another product or page count is not resumed', async () => {
    const { runScraping, createInMemoryStorage, END_REASON } = content;

    const pages = [makeComments(1, 1)];
    const storage = createInMemoryStorage();
    const recorder = createDownloadRecorder();
    const abortSignal = { stopped: false };

    await runScraping(
      { maxPages: 5, productKey: 'product-A', productTitle: 'A', resume: false },
      createTestDeps(content, {
        storage,
        fakePage: createFakePage(pages, { failAfterPage: 1 }),
        abortSignal,
        recorder
      })
    );

    const otherProduct = await runScraping(
      { maxPages: 5, productKey: 'product-B', productTitle: 'B', resume: true },
      createTestDeps(content, {
        storage,
        fakePage: createFakePage(pages, { failAfterPage: 1 }),
        abortSignal,
        recorder
      })
    );
    assert.equal(otherProduct.resumed, false);
    assert.equal(otherProduct.checkpoint.productKey, 'product-B');

    const otherPageCount = await runScraping(
      { maxPages: 9, productKey: 'product-B', productTitle: 'B', resume: true },
      createTestDeps(content, {
        storage,
        fakePage: createFakePage(Array.from({ length: 9 }, (_, idx) => makeComments(idx + 1, 1))),
        abortSignal,
        recorder
      })
    );
    assert.equal(otherPageCount.resumed, false);
    assert.equal(otherPageCount.endReason, END_REASON.COMPLETED);
  });
});

describe('chrome.storage.local callback adapter', () => {
  test('adapts callback-style get/set into promises used by the engine', async () => {
    const content = loadContentModule();
    const chromeStub = createChromeStorageStub();
    global.chrome = {
      storage: {
        local: chromeStub
      }
    };

    const adapter = content.createChromeStorageAdapter();
    await adapter.set({ scrapeCheckpoint: { nextPage: 7 }, other: 'keep' });
    const result = await adapter.get(['scrapeCheckpoint']);

    assert.deepEqual(result.scrapeCheckpoint, { nextPage: 7 });
    assert.equal('other' in result, false);

    // The engine can round-trip a checkpoint through the adapter
    const cp = content.createInitialCheckpoint({
      productKey: 'x',
      productTitle: 'X',
      maxPages: 10,
      previousComments: 3,
      now: () => 't'
    });
    await content.saveCheckpoint(adapter, cp);
    const loaded = await content.loadCheckpoint(adapter);
    assert.equal(loaded.nextPage, 1);
    assert.equal(loaded.totalComments, 3);

    await content.clearCheckpoint(adapter);
    const afterClear = await content.loadCheckpoint(adapter);
    assert.equal(afterClear, null);

    delete global.chrome;
  });
});
