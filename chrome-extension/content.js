// Content script to scrape Shopee comments
console.log('Shopee Scraper Extension: Content script loaded');

// Configuration
const CHUNK_SIZE = 100; // Scrape 100 pages per chunk
const SAVE_INTERVAL = CHUNK_SIZE; // Auto-save every 100 pages

// Checkpoint storage key + possible end reasons for a scraping session
const CHECKPOINT_KEY = 'scrapeCheckpoint';
const CHECKPOINT_VERSION = 1;
const END_REASON = {
  COMPLETED: 'completed',
  PAGINATION_FAILED: 'pagination_failed',
  USER_STOPPED: 'user_stopped'
};

// Wait for page to load completely
function waitForElement(selector, timeout = 10000) {
  return new Promise((resolve, reject) => {
    if (document.querySelector(selector)) {
      return resolve(document.querySelector(selector));
    }

    const observer = new MutationObserver(() => {
      if (document.querySelector(selector)) {
        observer.disconnect();
        resolve(document.querySelector(selector));
      }
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true
    });

    setTimeout(() => {
      observer.disconnect();
      reject(new Error(`Element ${selector} not found within ${timeout}ms`));
    }, timeout);
  });
}

// UTF-8 BOM used so Excel opens the CSV with the right encoding
const UTF8_BOM = '﻿';

// Build CSV text (without BOM) using the existing chunk CSV header
function buildCSVString(comments) {
  let csv = 'Username,Rating,Date,Comment\n';

  comments.forEach(comment => {
    const username = escapeCSV(comment.username || '');
    const rating = comment.rating || 0;
    const date = escapeCSV(comment.date || '');
    const commentText = escapeCSV(comment.comment || '');

    csv += `${username},${rating},${date},${commentText}\n`;
  });

  return csv;
}

// Helper function to generate CSV and trigger download.
// `partial` marks chunks exported because scraping ended early
// (pagination failure / user stop) before the chunk was finished.
function downloadCSVChunk(comments, chunkNumber, totalChunks, partial = false) {
  console.log(`[Download] Generating CSV for chunk ${chunkNumber}/${totalChunks}${partial ? ' (partial)' : ''}...`);

  const csv = buildCSVString(comments);

  // Generate filename with chunk number
  const timestamp = new Date().toISOString().split('T')[0];
  const partialSuffix = partial ? '_partial' : '';
  const filename = `shopee_comments_chunk_${chunkNumber}_of_${totalChunks}${partialSuffix}_${timestamp}.csv`;

  // Create blob and download (UTF-8 BOM convention preserved)
  const blob = new Blob([UTF8_BOM + csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);

  console.log(`[Download] ✅ Downloaded: ${filename}`);
  return filename;
}

// Helper function to escape CSV values
function escapeCSV(str) {
  if (str === null || str === undefined) return '';
  str = String(str);
  if (str.includes(',') || str.includes('"') || str.includes('\n')) {
    return '"' + str.replace(/"/g, '""') + '"';
  }
  return str;
}

// Scroll to rating section
async function scrollToRatings() {
  console.log('[Scroll] Looking for ratings section...');

  // Quick check for ratings section or comment container
  let ratingSection = document.querySelector('.product-ratings') ||
                      document.querySelector('[data-sqe="rating"]') ||
                      document.querySelector('.ejmN1R') ||
                      document.querySelector('.shopee-product-rating') ||
                      document.querySelector('.shopee-product-comment-list') ||
                      document.querySelector('[data-cmtid]');

  // If not immediately visible, scroll down to trigger Shopee lazy load
  if (!ratingSection) {
    console.log('[Scroll] Section not immediately visible, scrolling down to trigger lazy load...');
    window.scrollBy({ top: 800, behavior: 'smooth' });
    await sleep(1500);
    window.scrollBy({ top: 800, behavior: 'smooth' });
    await sleep(1500);

    ratingSection = document.querySelector('.product-ratings') ||
                    document.querySelector('[data-sqe="rating"]') ||
                    document.querySelector('.ejmN1R') ||
                    document.querySelector('.shopee-product-rating') ||
                    document.querySelector('.shopee-product-comment-list') ||
                    document.querySelector('[data-cmtid]');
  }

  if (ratingSection) {
    console.log('[Scroll] Found ratings section, scrolling into view...');
    ratingSection.scrollIntoView({ behavior: 'smooth', block: 'center' });
    await sleep(2000);

    // Scroll a bit more to ensure pagination is visible
    window.scrollBy({ top: 300, behavior: 'smooth' });
    await sleep(1000);

    console.log('[Scroll] ✅ Scrolled to ratings section');
    return true;
  }

  console.log('[Scroll] ❌ Ratings section not found on page');
  return false;
}

// Sleep function
function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// Find and click next pagination button
async function findAndClickNextButton(pageNumber) {
  console.log(`[Click] Looking for next button for page ${pageNumber}...`);

  let nextButton = null;

  // Strategy 1: Direct class selector
  nextButton = document.querySelector('.shopee-icon-button--right');

  // Strategy 2: Text-based fallback
  if (!nextButton || nextButton.disabled) {
    console.log('[Click] Strategy 1 failed, trying Strategy 2...');
    const allButtons = document.querySelectorAll('button');
    for (const button of allButtons) {
      const buttonText = button.textContent.trim();
      const isDisabled = button.disabled || button.classList.contains('shopee-button-no-outline--disabled');

      if (buttonText === '›' && !isDisabled) {
        nextButton = button;
        break;
      }
    }
  }

  // Strategy 3: Container-based fallback
  if (!nextButton || nextButton.disabled) {
    console.log('[Click] Strategy 2 failed, trying Strategy 3...');
    const paginationContainer = document.querySelector('.shopee-mini-page-controller');
    if (paginationContainer) {
      const buttons = paginationContainer.querySelectorAll('button:not([disabled])');
      const lastButton = buttons[buttons.length - 1];
      if (lastButton) {
        nextButton = lastButton;
      }
    }
  }

  if (!nextButton || nextButton.disabled) {
    console.log(`[Click] ❌ No enabled next button found`);
    return null;
  }

  // Found button, now click it
  console.log(`[Click] ✅ Found next button: ${nextButton.className}`);
  console.log(`[Click] Button text: "${nextButton.textContent.trim()}"`);

  // Scroll button into view
  nextButton.scrollIntoView({ behavior: 'smooth', block: 'center' });
  await sleep(500);

  // Multiple click methods for reliability
  console.log(`[Click] Clicking button...`);
  nextButton.focus();
  await sleep(100);

  nextButton.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
  await sleep(50);
  nextButton.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
  await sleep(50);

  nextButton.click();
  nextButton.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));

  console.log(`[Click] ✅ Button clicked for page ${pageNumber}`);

  // Scroll down to trigger lazy load
  await sleep(500);
  window.scrollBy({ top: 200, behavior: 'smooth' });

  return nextButton;
}

// Click "Lihat Semua" button to load more comments
async function clickLoadMore() {
  // Find the "Lihat Semua" or pagination buttons
  const loadMoreButtons = [
    ...document.querySelectorAll('button'),
    ...document.querySelectorAll('.shopee-button-solid--primary'),
    ...document.querySelectorAll('[class*="btn"]')
  ];

  for (const button of loadMoreButtons) {
    const text = button.textContent.trim().toLowerCase();
    if (text.includes('lihat semua') || text.includes('selanjutnya') || text.includes('next')) {
      button.click();
      await sleep(2000); // Wait for content to load
      return true;
    }
  }
  return false;
}

// Click pagination to load more comments
async function loadAllComments(maxPages = 10) {
  console.log(`[Pagination] Starting to load up to ${maxPages} pages...`);

  let currentPage = 1; // Start from page 1
  let previousCommentCount = 0;

  // Count initial comments
  const initialXPath = '//div[@data-cmtid]';
  let initialResult = document.evaluate(initialXPath, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
  previousCommentCount = initialResult.snapshotLength;
  console.log(`[Pagination] Initial comments on page 1: ${previousCommentCount}`);

  // Try to load more pages
  for (let i = 1; i < maxPages; i++) {
    console.log(`[Pagination] Attempting to load page ${i + 1}...`);

    // Find pagination button with class "shopee-icon-button--right"
    let nextButton = null;

    // Strategy 1: Find button with class "shopee-icon-button--right"
    nextButton = document.querySelector('.shopee-icon-button--right');

    // Strategy 2: Alternative - find button with "›" text
    if (!nextButton || nextButton.disabled) {
      console.log('[Pagination] Strategy 1 failed, trying Strategy 2...');
      const allButtons = document.querySelectorAll('button');
      for (const button of allButtons) {
        const buttonText = button.textContent.trim();
        const isDisabled = button.disabled || button.classList.contains('shopee-button-no-outline--disabled');

        // Check if it's a next button
        if (buttonText === '›' && !isDisabled) {
          nextButton = button;
          break;
        }
      }
    }

    // Strategy 3: Try pagination container
    if (!nextButton || nextButton.disabled) {
      console.log('[Pagination] Strategy 2 failed, trying Strategy 3...');
      const paginationContainer = document.querySelector('.shopee-mini-page-controller');
      if (paginationContainer) {
        const buttons = paginationContainer.querySelectorAll('button:not([disabled])');
        // Find the last enabled button
        const lastButton = buttons[buttons.length - 1];
        if (lastButton) {
          nextButton = lastButton;
        }
      }
    }

    if (!nextButton || nextButton.disabled) {
      console.log(`[Pagination] No enabled next button found. Stopping at page ${currentPage}`);
      break;
    }

    // Click the button
    console.log(`[Pagination] Found next button with class: ${nextButton.className}`);
    console.log(`[Pagination] Button disabled state: ${nextButton.disabled}`);
    console.log(`[Pagination] Button text: "${nextButton.textContent.trim()}"`);

    // Get current URL/state before click
    const urlBefore = window.location.href;
    console.log(`[Pagination] URL before click: ${urlBefore}`);

    // Scroll to pagination to ensure it's visible
    nextButton.scrollIntoView({ behavior: 'smooth', block: 'center' });
    await sleep(800); // Wait for scroll to complete

    console.log(`[Pagination] Clicking next button for page ${i + 1}...`);

    // Method 1: Focus then click
    nextButton.focus();
    await sleep(100);

    // Method 2: MouseDown + MouseUp (more realistic)
    nextButton.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
    await sleep(50);
    nextButton.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
    await sleep(50);

    // Method 3: Standard click
    nextButton.click();
    await sleep(50);

    // Method 4: Dispatch click event
    nextButton.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));

    console.log(`[Pagination] All click methods triggered`);

    // Check if URL changed
    await sleep(500);
    const urlAfter = window.location.href;
    console.log(`[Pagination] URL after click: ${urlAfter}`);
    console.log(`[Pagination] URL changed: ${urlBefore !== urlAfter}`);

    // Wait for new comments to load (4 seconds for slow networks)
    console.log(`[Pagination] Waiting 4 seconds for comments to load...`);
    await sleep(4000);

    // Scroll down a bit to trigger lazy loading
    console.log(`[Pagination] Scrolling down to trigger lazy load...`);
    window.scrollBy({ top: 200, behavior: 'smooth' });
    await sleep(1000);

    // Count comments after click
    const newResult = document.evaluate(initialXPath, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
    const newCommentCount = newResult.snapshotLength;

    console.log(`[Pagination] Comments after page ${i + 1}: ${newCommentCount} (previous: ${previousCommentCount})`);

    // Check if comments actually increased
    if (newCommentCount > previousCommentCount) {
      console.log(`[Pagination] ✅ Successfully loaded ${newCommentCount - previousCommentCount} new comments`);
      previousCommentCount = newCommentCount;
      currentPage = i + 1;
    } else {
      console.log(`[Pagination] ⚠️ No new comments loaded after 4s. Trying aggressive retry...`);

      // Aggressive retry: scroll more and wait longer
      console.log(`[Pagination] Scrolling down more aggressively...`);
      window.scrollBy({ top: 500, behavior: 'smooth' });
      await sleep(1000);
      window.scrollBy({ top: -200, behavior: 'smooth' });
      await sleep(3000);

      const retryResult = document.evaluate(initialXPath, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
      const retryCommentCount = retryResult.snapshotLength;

      console.log(`[Pagination] Comments after aggressive retry: ${retryCommentCount}`);

      if (retryCommentCount > previousCommentCount) {
        console.log(`[Pagination] ✅ Comments loaded after retry: ${retryCommentCount - previousCommentCount} new comments`);
        previousCommentCount = retryCommentCount;
        currentPage = i + 1;
      } else {
        console.log(`[Pagination] ❌ Still no new comments after all attempts`);
        console.log(`[Pagination] Possible reasons:`);
        console.log(`  - Button click not working (check if button is actually clickable)`);
        console.log(`  - No more pages available (only 1 page of comments)`);
        console.log(`  - Shopee changed their pagination system`);
        console.log(`  - Network issue preventing data load`);
        break;
      }
    }

    // Extra wait between pages
    await sleep(500);
  }

  console.log(`[Pagination] Finished. Total pages loaded: ${currentPage}, Total comments: ${previousCommentCount}`);
  return currentPage;
}

// Count comment containers currently rendered in the DOM
function countCommentElements() {
  const result = document.evaluate(
    '//div[@data-cmtid]',
    document,
    null,
    XPathResult.ORDERED_NODE_SNAPSHOT_TYPE,
    null
  );
  return result.snapshotLength;
}

// Extract comment data with duplicate tracking
function extractCommentsWithTracking(extractedIds) {
  console.log('[Extract] Starting comment extraction with tracking...');
  const comments = [];

  // Use XPath to find ALL comment containers in DOM
  const xpath = '//div[@data-cmtid]';
  const xpathResult = document.evaluate(xpath, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);

  let commentElements = [];
  for (let i = 0; i < xpathResult.snapshotLength; i++) {
    commentElements.push(xpathResult.snapshotItem(i));
  }

  console.log(`[Extract] XPath found ${commentElements.length} comment containers in DOM`);
  console.log(`[Extract] Already extracted: ${extractedIds.size} comments`);

  // Fallback to CSS selector if XPath returns nothing
  if (commentElements.length === 0) {
    console.log('[Extract] XPath found no comments, trying CSS selector fallback...');
    commentElements = Array.from(document.querySelectorAll('[data-cmtid]'));
    console.log(`[Extract] CSS selector found ${commentElements.length} comment containers`);
  }

  if (commentElements.length === 0) {
    console.log('[Extract] ❌ No comments found in DOM');
    return comments;
  }

  console.log(`[Extract] Processing ${commentElements.length} comment elements...`);
  let successCount = 0;
  let failCount = 0;
  let duplicateCount = 0;

  commentElements.forEach((element, index) => {
    try {
      const cmtid = element.getAttribute('data-cmtid');

      // Skip if already extracted
      if (extractedIds.has(cmtid)) {
        duplicateCount++;
        console.log(`[Extract] ⏭️  #${index + 1}: Skipped (cmtid: ${cmtid}) - Already extracted`);
        return;
      }

      console.log(`[Extract] Processing comment ${index + 1}/${commentElements.length} (cmtid: ${cmtid})`);

      // Extract username using XPath
      let username = '';
      const usernameXPath = './/div[contains(text(), "|")]/parent::div/parent::div/*[1]';
      const usernameResult = document.evaluate(usernameXPath, element, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
      if (usernameResult.singleNodeValue) {
        username = usernameResult.singleNodeValue.textContent.trim();
      }

      // Extract date and variant using XPath
      let date = '';
      const dateXPath = './/div[contains(text(), "|")]';
      const dateResult = document.evaluate(dateXPath, element, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
      if (dateResult.singleNodeValue) {
        date = dateResult.singleNodeValue.textContent.trim();
      }

      // Extract comment body using XPath
      let commentText = '';
      const bodyXPath = './/div[contains(text(), "|")]/parent::div/parent::div/following-sibling::div[1]';
      const bodyResult = document.evaluate(bodyXPath, element, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
      if (bodyResult.singleNodeValue) {
        commentText = bodyResult.singleNodeValue.textContent.trim();
      }

      // Extract rating by counting SVG stars using XPath
      let rating = 0;
      const ratingXPath = './/svg[contains(@class, "icon-rating-solid")]';
      const ratingResult = document.evaluate(ratingXPath, element, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
      rating = ratingResult.snapshotLength;

      // Only add if we have meaningful comment text
      if (commentText && commentText.length > 5) {
        const comment = {
          username: username,
          rating: rating,
          date: date,
          comment: commentText.replace(/[\r\n]+/g, ' ').trim()
        };

        comments.push(comment);
        extractedIds.add(cmtid); // Mark as extracted

        successCount++;
        console.log(`[Extract] ✅ #${index + 1}: ${username} (${rating}★) - ${commentText.substring(0, 40)}...`);
      } else {
        failCount++;
        console.log(`[Extract] ⚠️ #${index + 1}: Skipped - no comment text (username: ${username}, date: ${date})`);
      }
    } catch (error) {
      failCount++;
      console.error(`[Extract] ❌ Error extracting comment ${index + 1}:`, error);
    }
  });

  console.log(`[Extract] ===== Extraction Summary =====`);
  console.log(`[Extract] Total in DOM: ${commentElements.length}`);
  console.log(`[Extract] New comments: ${successCount}`);
  console.log(`[Extract] Duplicates: ${duplicateCount}`);
  console.log(`[Extract] Failed: ${failCount}`);
  console.log(`[Extract] Total extracted (cumulative): ${extractedIds.size}`);

  return comments;
}

// Extract comment data from the page using XPath approach (OLD - keep for fallback)
function extractComments() {
  console.log('[Extract] Starting comment extraction...');
  const comments = [];

  // Method 1: Try using XPath to find comment containers
  const xpath = '//div[@data-cmtid]';
  const xpathResult = document.evaluate(xpath, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);

  let commentElements = [];
  for (let i = 0; i < xpathResult.snapshotLength; i++) {
    commentElements.push(xpathResult.snapshotItem(i));
  }

  console.log(`[Extract] XPath found ${commentElements.length} comment containers`);

  // Method 2: Fallback to querySelectorAll if XPath returns nothing
  if (commentElements.length === 0) {
    console.log('[Extract] XPath found no comments, trying CSS selector fallback...');
    const commentList = document.querySelector('.shopee-product-comment-list');
    if (commentList) {
      commentElements = Array.from(commentList.children).filter(el => el.hasAttribute('data-cmtid'));
      console.log(`[Extract] CSS selector found ${commentElements.length} comment containers`);
    }
  }

  if (commentElements.length === 0) {
    console.log('[Extract] ❌ No comments found with data-cmtid attribute');
    return comments;
  }

  console.log(`[Extract] Processing ${commentElements.length} comment elements...`);
  let successCount = 0;
  let failCount = 0;

  commentElements.forEach((element, index) => {
    try {
      const cmtid = element.getAttribute('data-cmtid');
      console.log(`[Extract] Processing comment ${index + 1}/${commentElements.length} (cmtid: ${cmtid})`);

      // Extract username using XPath
      let username = 'Unknown';
      const usernameXPath = './/div[contains(text(), "|")]/parent::div/parent::div/*[1]';
      const usernameResult = document.evaluate(usernameXPath, element, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
      if (usernameResult.singleNodeValue) {
        username = usernameResult.singleNodeValue.textContent.trim();
      }

      // Extract date and variant using XPath
      let date = '';
      const dateXPath = './/div[contains(text(), "|")]';
      const dateResult = document.evaluate(dateXPath, element, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
      if (dateResult.singleNodeValue) {
        date = dateResult.singleNodeValue.textContent.trim();
      }

      // Extract comment body using XPath
      let commentText = '';
      const bodyXPath = './/div[contains(text(), "|")]/parent::div/parent::div/following-sibling::div[1]';
      const bodyResult = document.evaluate(bodyXPath, element, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
      if (bodyResult.singleNodeValue) {
        commentText = bodyResult.singleNodeValue.textContent.trim();
      }

      // Extract rating by counting SVG stars using XPath
      let rating = 0;
      const ratingXPath = './/svg[contains(@class, "icon-rating-solid")]';
      const ratingResult = document.evaluate(ratingXPath, element, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
      rating = ratingResult.snapshotLength;

      // Only add if we have meaningful comment text
      if (commentText && commentText.length > 5) {
        comments.push({
          username: username,
          rating: rating,
          date: date,
          comment: commentText.replace(/[\r\n]+/g, ' ').trim()
        });

        successCount++;
        console.log(`[Extract] ✅ #${index + 1}: ${username} (${rating}★) - ${commentText.substring(0, 40)}...`);
      } else {
        failCount++;
        console.log(`[Extract] ⚠️ #${index + 1}: Skipped - no comment text (username: ${username}, date: ${date})`);
      }
    } catch (error) {
      failCount++;
      console.error(`[Extract] ❌ Error extracting comment ${index + 1}:`, error);
    }
  });

  console.log(`[Extract] ✅ Extraction complete: ${successCount} success, ${failCount} failed, ${comments.length} total valid comments`);
  return comments;
}

// Abort flag for cancellation
let isScrapingAborted = false;

// Extract Shopee Product Title for smart naming
function getShopeeProductTitle() {
  const h1 = document.querySelector('h1') || document.querySelector('.WB_tW') || document.querySelector('[data-sqe="name"]');
  if (h1 && h1.textContent) {
    return h1.textContent.trim().replace(/[^a-zA-Z0-9_ -]/g, '').substring(0, 40);
  }
  return 'Shopee_Product';
}

// Stable identifier of the product page being scraped (used to scope checkpoints)
function getProductKey() {
  try {
    return window.location.origin + window.location.pathname;
  } catch (error) {
    return 'unknown-product';
  }
}

function chunkNumberOf(page) {
  return Math.floor((page - 1) / CHUNK_SIZE) + 1;
}

// Promise-based adapter over chrome.storage.local
function createChromeStorageAdapter() {
  return {
    get(keys) {
      return new Promise(resolve => {
        chrome.storage.local.get(keys, result => resolve(result || {}));
      });
    },
    set(items) {
      return new Promise(resolve => {
        chrome.storage.local.set(items, () => resolve());
      });
    }
  };
}

// Simple in-memory implementation of the same storage contract (used by tests)
function createInMemoryStorage(initial = {}) {
  const store = new Map(Object.entries(initial));
  return {
    store,
    async get(keys) {
      if (keys === null || keys === undefined) {
        return Object.fromEntries(store);
      }
      const keyList = Array.isArray(keys) ? keys : [keys];
      const result = {};
      keyList.forEach(key => {
        if (store.has(key)) result[key] = store.get(key);
      });
      return result;
    },
    async set(items) {
      Object.entries(items).forEach(([key, value]) => store.set(key, value));
    },
    async remove(keys) {
      const keyList = Array.isArray(keys) ? keys : [keys];
      keyList.forEach(key => store.delete(key));
    }
  };
}

async function loadCheckpoint(storage) {
  const result = await storage.get([CHECKPOINT_KEY]);
  return result && result[CHECKPOINT_KEY] ? result[CHECKPOINT_KEY] : null;
}

async function saveCheckpoint(storage, checkpoint) {
  await storage.set({ [CHECKPOINT_KEY]: checkpoint });
}

async function clearCheckpoint(storage) {
  if (typeof storage.remove === 'function') {
    await storage.remove([CHECKPOINT_KEY]);
  } else {
    await storage.set({ [CHECKPOINT_KEY]: null });
  }
}

function createInitialCheckpoint({ productKey, productTitle, maxPages, previousComments, now }) {
  return {
    version: CHECKPOINT_VERSION,
    productKey,
    productTitle,
    maxPages,
    currentChunk: 1,
    nextPage: 1,
    lastCompletedPage: 0,
    totalComments: previousComments,
    exportedChunks: [],
    endedEarly: false,
    endReason: null,
    completed: false,
    createdAt: now(),
    updatedAt: now()
  };
}

/**
 * Core chunked scraping engine. All browser/DOM interactions are injected
 * through `deps` so the same logic runs against the real Shopee page and
 * against local fakes in tests.
 *
 * options:
 *   maxPages, productKey, productTitle, resume, previousComments
 * deps:
 *   storage       - { get, set } promise-based storage
 *   extractPage   - async () => comment[] extracted from the current page
 *   advancePage   - async (targetPage) => { ok: boolean, reason?: string }
 *   downloadCSV   - (comments, chunkNumber, totalChunks, partial) => filename
 *   isAborted     - () => boolean (user stop signal)
 *   onProgress    - optional (progress) => void
 *   resetDedup    - optional () => void, called at the start of each chunk
 *   sleep         - optional async (ms) => void
 *   now           - optional () => timestamp string
 */
async function runScraping(options, deps) {
  const {
    maxPages,
    productKey = 'unknown-product',
    productTitle = 'Shopee_Product',
    resume = false,
    previousComments = 0
  } = options;

  const {
    storage,
    extractPage,
    advancePage,
    downloadCSV,
    isAborted,
    onProgress = null,
    resetDedup = null,
    sleep: sleepFn = sleep,
    now = () => new Date().toISOString()
  } = deps;

  const totalChunks = Math.ceil(maxPages / CHUNK_SIZE);

  // Resume from an unfinished checkpoint only when it matches this run
  const existing = await loadCheckpoint(storage);
  const canResume = resume &&
    existing &&
    !existing.completed &&
    existing.productKey === productKey &&
    existing.maxPages === maxPages;

  let cp;
  if (canResume) {
    cp = existing;
    console.log(`[Scraper] ♻️ Resuming at page ${cp.nextPage}, chunk ${cp.currentChunk}`);
  } else {
    cp = createInitialCheckpoint({
      productKey,
      productTitle,
      maxPages,
      previousComments: typeof previousComments === 'number'
        ? previousComments
        : (previousComments || []).length,
      now
    });
    await saveCheckpoint(storage, cp);
  }

  const persist = async () => {
    cp.updatedAt = now();
    await saveCheckpoint(storage, cp);
  };

  const collectedComments = [];
  const result = {
    success: true,
    resumed: Boolean(canResume),
    endReason: END_REASON.COMPLETED,
    pagesLoaded: cp.lastCompletedPage,
    comments: collectedComments,
    count: cp.totalComments,
    chunkDetails: cp.exportedChunks,
    checkpoint: cp
  };

  const emitProgress = statusMessage => {
    if (onProgress) {
      onProgress({
        pagesLoaded: cp.lastCompletedPage,
        totalPages: maxPages,
        commentsCount: cp.totalComments,
        productTitle,
        statusMessage
      });
    }
  };

  // Page currently shown by the (real or fake) DOM. A fresh page always
  // shows page 1; resume runs must fast-forward to cp.nextPage first.
  let currentPage = 1;
  let startChunk = chunkNumberOf(Math.max(cp.nextPage, 1));
  let sessionEndReason = null;

  outer:
  for (let chunkNum = startChunk; chunkNum <= totalChunks; chunkNum++) {
    if (isAborted()) {
      sessionEndReason = END_REASON.USER_STOPPED;
      break;
    }

    cp.currentChunk = chunkNum;
    await persist();
    if (resetDedup) resetDedup();

    const chunkStartPage = (chunkNum - 1) * CHUNK_SIZE + 1;
    const chunkEndPage = Math.min(chunkNum * CHUNK_SIZE, maxPages);
    const chunkComments = [];
    let firstPageWithData = null;

    console.log(`\n${'='.repeat(60)}`);
    console.log(`[Scraper] 📦 CHUNK ${chunkNum}/${totalChunks} (pages ${chunkStartPage}-${chunkEndPage})`);
    console.log(`${'='.repeat(60)}`);

    // Fast-forward the DOM to the resume point without exporting anything
    while (currentPage < cp.nextPage) {
      if (isAborted()) {
        sessionEndReason = END_REASON.USER_STOPPED;
        break outer;
      }
      console.log(`[Scraper] ♻️ Fast-forwarding past page ${currentPage} toward ${cp.nextPage}...`);
      const advance = await advancePage(currentPage + 1);
      if (isAborted()) {
        sessionEndReason = END_REASON.USER_STOPPED;
        break outer;
      }
      if (!advance.ok) {
        console.log(`[Scraper] ❌ Fast-forward failed at page ${currentPage}: ${advance.reason || 'unknown'}`);
        sessionEndReason = END_REASON.PAGINATION_FAILED;
        break outer;
      }
      currentPage++;
    }

    let page = currentPage;
    let chunkEndedEarly = false;

    chunkLoop:
    while (page <= chunkEndPage) {
      if (isAborted()) {
        sessionEndReason = END_REASON.USER_STOPPED;
        chunkEndedEarly = true;
        break chunkLoop;
      }

      const pageComments = await extractPage();

      if (pageComments.length > 0) {
        chunkComments.push(...pageComments);
        collectedComments.push(...pageComments);
        cp.totalComments += pageComments.length;
        if (firstPageWithData === null) firstPageWithData = page;
      } else if (page === 1 && cp.lastCompletedPage === 0) {
        // An empty first page is allowed: nothing has been collected yet.
        console.log('[Scraper] ⚠️ Page 1 yielded no comments');
      } else {
        // Waiting did not produce any new comments -> pagination failure.
        console.log(`[Scraper] ❌ No new comments on page ${page}, treating as pagination failure`);
        sessionEndReason = END_REASON.PAGINATION_FAILED;
        chunkEndedEarly = true;
        break chunkLoop;
      }

      cp.lastCompletedPage = page;
      cp.nextPage = page + 1;
      result.pagesLoaded = page;
      await persist();
      emitProgress(`Halaman ${page}/${maxPages} selesai (${cp.totalComments} ulasan)`);

      if (page >= chunkEndPage) break chunkLoop;

      const advance = await advancePage(page + 1);
      if (isAborted()) {
        sessionEndReason = END_REASON.USER_STOPPED;
        chunkEndedEarly = true;
        break chunkLoop;
      }
      if (!advance.ok) {
        console.log(`[Scraper] ❌ Pagination failed moving to page ${page + 1}: ${advance.reason || 'unknown'}`);
        sessionEndReason = END_REASON.PAGINATION_FAILED;
        chunkEndedEarly = true;
        break chunkLoop;
      }
      page++;
      currentPage = page;
    }

    // Export the comments collected in this run. A chunk that was only
    // partially exported on a previous run can produce continuation
    // exports (different page range, no duplicated comments); a chunk
    // whose full page range is already covered by full exports is never
    // exported again.
    const entryStart = firstPageWithData || chunkStartPage;
    const entryEnd = cp.lastCompletedPage;
    const hasFullExport = cp.exportedChunks.some(
      entry => entry.chunkNumber === chunkNum && !entry.partial
    );
    if (chunkComments.length > 0 && !hasFullExport) {
      // Temporarily register this entry, then check whether all exports
      // for this chunk now cover the complete page range contiguously.
      const candidate = {
        chunkNumber: chunkNum,
        commentCount: chunkComments.length,
        startPage: entryStart,
        endPage: entryEnd,
        partial: true,
        filename: null,
        exportedAt: now()
      };
      const ranges = cp.exportedChunks
        .filter(entry => entry.chunkNumber === chunkNum)
        .map(entry => [entry.startPage, entry.endPage])
        .concat([[entryStart, entryEnd]])
        .sort((a, b) => a[0] - b[0]);

      let coversFullRange = ranges[0][0] === chunkStartPage;
      for (let i = 1; i < ranges.length && coversFullRange; i++) {
        if (ranges[i][0] > ranges[i - 1][1] + 1) coversFullRange = false;
      }
      if (coversFullRange && ranges[ranges.length - 1][1] !== chunkEndPage) {
        coversFullRange = false;
      }

      const partial = chunkEndedEarly || !coversFullRange;
      candidate.partial = partial;
      const filename = downloadCSV(chunkComments, chunkNum, totalChunks, partial);
      candidate.filename = filename;
      cp.exportedChunks.push(candidate);
      result.chunkDetails = cp.exportedChunks;
      console.log(`[Scraper] ✅ Exported chunk ${chunkNum} (${chunkComments.length} comments, pages ${entryStart}-${entryEnd}${partial ? ', partial' : ''})`);
    } else if (chunkComments.length === 0) {
      console.log(`[Scraper] ⏭️ No new comments for chunk ${chunkNum}, nothing to export`);
    } else {
      console.log(`[Scraper] ⏭️ Chunk ${chunkNum} already fully exported, skipping download`);
    }

    await persist();

    if (chunkEndedEarly) {
      break outer;
    }

    if (cp.nextPage > maxPages) {
      break outer;
    }

    if (chunkNum < totalChunks) {
      await sleepFn(3000);
    }
  }

  // Finalize checkpoint state
  if (sessionEndReason) {
    cp.endedEarly = true;
    cp.endReason = sessionEndReason;
    result.endReason = sessionEndReason;
  } else {
    cp.endedEarly = false;
    cp.endReason = END_REASON.COMPLETED;
    cp.completed = true;
    result.endReason = END_REASON.COMPLETED;
  }
  result.count = cp.totalComments;
  result.pagesLoaded = cp.lastCompletedPage;
  result.checkpoint = cp;
  await persist();

  return result;
}

// Production deps: wire the engine to the real Shopee DOM
function createProductionDeps({ storage, productTitle, maxPages }) {
  let extractedIds = new Set();

  return {
    storage,
    isAborted: () => isScrapingAborted,
    sleep,
    now: () => new Date().toISOString(),
    resetDedup() {
      extractedIds = new Set();
    },
    async extractPage() {
      let comments = extractCommentsWithTracking(extractedIds);
      if (comments.length === 0) {
        // Preserve the original retry-once behavior
        await sleep(2000);
        comments = extractCommentsWithTracking(extractedIds);
      }
      return comments;
    },
    async advancePage(targetPage) {
      const commentCountBefore = countCommentElements();

      const nextButton = await findAndClickNextButton(targetPage);
      if (!nextButton || nextButton.disabled) {
        return { ok: false, reason: 'no_next_button' };
      }

      console.log(`[Scraper] Waiting 5 seconds for page ${targetPage} to load...`);
      await sleep(5000);
      if (isScrapingAborted) {
        return { ok: false, reason: 'aborted' };
      }

      // Scroll to ensure render
      window.scrollBy({ top: 300, behavior: 'smooth' });
      await sleep(1000);
      window.scrollBy({ top: -100, behavior: 'smooth' });
      await sleep(1000);

      let commentCountAfter = countCommentElements();
      if (commentCountAfter > commentCountBefore) {
        return { ok: true };
      }

      // Aggressive retry, mirroring the previous pagination behavior
      console.log('[Scraper] ⚠️ Comment count did not increase, retrying aggressively...');
      window.scrollBy({ top: 500, behavior: 'smooth' });
      await sleep(1000);
      window.scrollBy({ top: -200, behavior: 'smooth' });
      await sleep(3000);

      commentCountAfter = countCommentElements();
      if (commentCountAfter > commentCountBefore) {
        return { ok: true };
      }

      return { ok: false, reason: 'no_new_comments' };
    },
    downloadCSV: (comments, chunkNumber, totalChunks, partial) =>
      downloadCSVChunk(comments, chunkNumber, totalChunks, partial),
    onProgress(progress) {
      chrome.storage.local.set({
        isScraping: true,
        currentPagesLoaded: progress.pagesLoaded,
        totalPagesRequested: progress.totalPages,
        comments: [],
        productName: productTitle,
        statusMessage: progress.statusMessage
      });
    }
  };
}

// Main scraping function with chunking support for large datasets
async function scrapeComments(maxPages = 10) {
  try {
    isScrapingAborted = false;
    console.log('[Scraper] Starting comment scraping with chunking support...');
    console.log(`[Scraper] Total pages requested: ${maxPages}`);

    const productTitle = getShopeeProductTitle();
    const productKey = getProductKey();

    // Scroll to ratings section & verify existence
    const hasRatingsSection = await scrollToRatings();
    if (!hasRatingsSection) {
      console.log('[Scraper] ❌ Ratings section not found. Aborting scraping.');
      chrome.storage.local.set({
        isScraping: false,
        statusMessage: '❌ Gagal: Section ulasan/komentar produk tidak ditemukan di halaman ini! Pastikan Anda berada di halaman produk Shopee yang Memiliki Ulasan.'
      });
      return {
        success: false,
        error: 'Section ulasan/komentar tidak ditemukan pada halaman ini! Pastikan Anda membuka halaman produk Shopee yang memiliki ulasan/penilaian.'
      };
    }

    await sleep(2000);

    const storage = createChromeStorageAdapter();
    const existingCheckpoint = await loadCheckpoint(storage);
    const resume = Boolean(
      existingCheckpoint &&
      !existingCheckpoint.completed &&
      existingCheckpoint.productKey === productKey &&
      existingCheckpoint.maxPages === maxPages
    );

    let previousCommentCount = 0;
    if (resume) {
      previousCommentCount = existingCheckpoint.totalComments || 0;
      console.log(`[Scraper] ♻️ Unfinished checkpoint found, resuming from page ${existingCheckpoint.nextPage}`);
      chrome.storage.local.set({
        isScraping: true,
        currentPagesLoaded: existingCheckpoint.lastCompletedPage,
        totalPagesRequested: maxPages,
        productName: productTitle,
        statusMessage: `Melanjutkan scraping dari halaman ${existingCheckpoint.nextPage}...`
      });
    } else {
      chrome.storage.local.set({
        isScraping: true,
        currentPagesLoaded: 1,
        totalPagesRequested: maxPages,
        comments: [],
        productName: productTitle,
        statusMessage: `Memulai scraping ${maxPages} halaman...`
      });
    }

    const deps = createProductionDeps({ storage, productTitle, maxPages });
    const engineResult = await runScraping(
      {
        maxPages,
        productKey,
        productTitle,
        resume,
        previousComments: previousCommentCount
      },
      deps
    );

    const cp = engineResult.checkpoint;
    const aborted = engineResult.endReason === END_REASON.USER_STOPPED;

    let statusMessage;
    if (engineResult.endReason === END_REASON.PAGINATION_FAILED) {
      statusMessage = `Halaman habis/gagal dimuat. ${cp.totalComments} ulasan dari ${cp.lastCompletedPage} halaman tersimpan (chunk terekspor: ${cp.exportedChunks.length}).`;
    } else if (aborted) {
      statusMessage = `Dihentikan. ${cp.totalComments} ulasan dari ${cp.lastCompletedPage} halaman.`;
    } else {
      statusMessage = `Selesai! ${cp.totalComments} ulasan dari ${cp.lastCompletedPage} halaman.`;
    }

    // Update storage upon finish
    chrome.storage.local.set({
      isScraping: false,
      currentPagesLoaded: cp.lastCompletedPage,
      totalPagesRequested: maxPages,
      productName: productTitle,
      statusMessage
    });

    console.log(`[Scraper] Finished (${engineResult.endReason}). Total collected: ${cp.totalComments}`);

    return {
      success: true,
      aborted,
      endReason: engineResult.endReason,
      resumed: engineResult.resumed,
      comments: engineResult.comments,
      count: cp.totalComments,
      pagesLoaded: cp.lastCompletedPage,
      productTitle,
      chunks: cp.exportedChunks.length,
      chunkDetails: cp.exportedChunks,
      checkpoint: cp,
      message: statusMessage
    };
  } catch (error) {
    console.error('[Scraper] Error during scraping:', error);
    return {
      success: false,
      error: error.message,
      comments: [],
      count: 0
    };
  }
}

// Listen for messages from popup (browser context only)
if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  console.log('Content script received message:', request);

  if (request.action === 'scrapeComments') {
    isScrapingAborted = false;
    scrapeComments(request.maxPages || 10).then(result => {
      console.log('Scraping result:', result);
      sendResponse(result);
    });
    return true; // Keep message channel open for async response
  }

  if (request.action === 'stopScraping') {
    isScrapingAborted = true;
    console.log('Content script: Received stopScraping signal');
    sendResponse({ success: true, message: 'Stop signal received' });
    return true;
  }

  if (request.action === 'getScrapeCheckpoint') {
    loadCheckpoint(createChromeStorageAdapter())
      .then(checkpoint => sendResponse({ success: true, checkpoint }));
    return true;
  }
});
}

// Export pure logic for Node-based tests (no effect inside the extension)
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    CHUNK_SIZE,
    CHECKPOINT_KEY,
    CHECKPOINT_VERSION,
    END_REASON,
    UTF8_BOM,
    escapeCSV,
    buildCSVString,
    downloadCSVChunk,
    chunkNumberOf,
    createInitialCheckpoint,
    createChromeStorageAdapter,
    createInMemoryStorage,
    loadCheckpoint,
    saveCheckpoint,
    clearCheckpoint,
    runScraping
  };
}
