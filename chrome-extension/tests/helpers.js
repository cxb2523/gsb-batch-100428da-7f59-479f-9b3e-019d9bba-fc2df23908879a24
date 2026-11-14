'use strict';

// Test helpers: local fakes for the Shopee DOM and chrome.storage.local.
// Nothing here touches a real browser or the Shopee page.

const path = require('path');

const contentPath = path.join(__dirname, '..', 'content.js');

function loadContentModule() {
  // Each call gets a fresh module state.
  delete require.cache[require.resolve(contentPath)];
  return require(contentPath);
}

// Build N fake comment objects for a given page number.
function makeComments(page, count) {
  const comments = [];
  for (let i = 1; i <= count; i++) {
    comments.push({
      username: `user_p${page}_${i}`,
      rating: (i % 5) + 1,
      date: `2026-11-${String((page % 27) + 1).padStart(2, '0')}`,
      comment: `Komentar halaman ${page} nomor ${i}, cukup panjang untuk lolos filter`
    });
  }
  return comments;
}

/**
 * Fake page environment.
 *
 * pages: array of comment arrays (index 0 == page 1)
 * options:
 *   failAfterPage  - advancePage() fails once the DOM would move past this
 *                    page (no working next-page button / no new comments)
 *   noGrowthPages  - set of target pages where clicking succeeds but the
 *                    comment count does not increase
 */
function createFakePage(pages, options = {}) {
  const failAfterPage = options.failAfterPage === undefined ? Infinity : options.failAfterPage;
  const noGrowthPages = new Set(options.noGrowthPages || []);
  const pageCommentCount = pages.map(group => group.length);
  let currentPage = 1;
  const advanceLog = [];

  function countComments() {
    let total = 0;
    for (let p = 1; p <= currentPage; p++) {
      total += pageCommentCount[p - 1] || 0;
    }
    return total;
  }

  return {
    get currentPage() {
      return currentPage;
    },
    advanceLog,
    countComments,
    // Resolves to { ok, reason } like the production advancePage dep
    async advancePage(targetPage) {
      advanceLog.push(targetPage);
      if (targetPage > failAfterPage + 1) {
        return { ok: false, reason: 'no_next_button' };
      }
      if (noGrowthPages.has(targetPage)) {
        return { ok: false, reason: 'no_new_comments' };
      }
      if (targetPage > pages.length) {
        return { ok: false, reason: 'no_next_button' };
      }
      currentPage = targetPage;
      return { ok: true };
    },
    // Returns only the comments of the current page (page-scoped extraction)
    async extractPage() {
      return pages[currentPage - 1] ? [...pages[currentPage - 1]] : [];
    }
  };
}

// Records every CSV export instead of triggering a browser download.
function createDownloadRecorder() {
  const downloads = [];
  return {
    downloads,
    downloadCSV(comments, chunkNumber, totalChunks, partial) {
      const filename = `chunk_${chunkNumber}_of_${totalChunks}${partial ? '_partial' : ''}.csv`;
      // Deep copy so later engine bookkeeping cannot mutate captured data
      downloads.push({
        filename,
        chunkNumber,
        totalChunks,
        partial: Boolean(partial),
        comments: comments.map(comment => ({ ...comment }))
      });
      return filename;
    }
  };
}

/**
 * Build the full deps object for runScraping().
 * `abortSignal` is an object whose `stopped` flag can be flipped mid-run
 * to simulate the user pressing "Hentikan Process".
 */
function createTestDeps(content, { storage, fakePage, abortSignal, recorder }) {
  return {
    storage,
    extractPage: async () => fakePage.extractPage(),
    advancePage: async targetPage => fakePage.advancePage(targetPage),
    downloadCSV: recorder.downloadCSV.bind(recorder),
    isAborted: () => abortSignal.stopped,
    onProgress: () => {},
    sleep: async () => {},
    now: () => '2026-11-15T00:00:00.000Z'
  };
}

// Callback-style chrome.storage.local stub (mirrors the real extension API)
function createChromeStorageStub(initial = {}) {
  const data = { ...initial };
  const calls = [];
  return {
    calls,
    data,
    get(keys, callback) {
      calls.push({ method: 'get', keys });
      const keyList = Array.isArray(keys) ? keys : [keys];
      const result = {};
      keyList.forEach(key => {
        if (Object.prototype.hasOwnProperty.call(data, key)) {
          result[key] = data[key];
        }
      });
      callback(result);
    },
    set(items, callback) {
      calls.push({ method: 'set', items });
      Object.assign(data, items);
      if (callback) callback();
    },
    remove(keys, callback) {
      calls.push({ method: 'remove', keys });
      const keyList = Array.isArray(keys) ? keys : [keys];
      keyList.forEach(key => delete data[key]);
      if (callback) callback();
    }
  };
}

module.exports = {
  loadContentModule,
  makeComments,
  createFakePage,
  createDownloadRecorder,
  createTestDeps,
  createChromeStorageStub
};
