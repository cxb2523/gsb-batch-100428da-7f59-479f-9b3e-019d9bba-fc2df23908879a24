/**
 * Node HTTP client for the local Douyin HTTP API.
 *
 * Design goals:
 * - never read cookies or credentials directly; the Python service owns auth;
 * - give agents one stable JSON boundary for search / frames / download;
 * - start the local HTTP service on demand so callers don't need to know how.
 */

import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { normalizeAweme, normalizeHotItems, normalizeSearchPage, normalizeUser } from './normalize.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SERVER_SCRIPT = path.join(ROOT, 'python', 'douyin_http.py');
const DATA_DIR = process.env.DOUYIN_DATA_DIR
  ? path.resolve(process.env.DOUYIN_DATA_DIR)
  : path.join(ROOT, 'data');

function findPython() {
  if (process.env.DOUYIN_PYTHON) return path.resolve(process.env.DOUYIN_PYTHON);
  const candidates = [
    path.join(ROOT, '.venv', 'Scripts', 'python.exe'),
    path.join(ROOT, '.venv', 'bin', 'python'),
    path.join(ROOT, '.venv', 'bin', 'python3'),
  ];
  for (const candidate of candidates) if (existsSync(candidate)) return candidate;
  return 'python';
}

export function baseUrl({ host, port } = {}) {
  if (process.env.DOUYIN_HTTP_BASE) return String(process.env.DOUYIN_HTTP_BASE).replace(/\/+$/, '');
  const usedHost = host ?? process.env.DOUYIN_HTTP_HOST ?? '127.0.0.1';
  const usedPort = port ?? process.env.DOUYIN_HTTP_PORT ?? '7709';
  return `http://${usedHost}:${usedPort}`;
}

async function probeHealth(base) {
  try {
    const res = await fetch(`${base}/health`, { signal: AbortSignal.timeout(1200) });
    return res.ok;
  } catch {
    return false;
  }
}

function spawnServer(base) {
  if (!existsSync(SERVER_SCRIPT)) {
    throw new Error(`douyin HTTP 脚本不存在：${SERVER_SCRIPT}`);
  }
  const py = findPython();
  const child = spawn(py, [SERVER_SCRIPT], {
    cwd: ROOT,
    env: {
      ...process.env,
      PYTHONUTF8: '1',
      PYTHONIOENCODING: 'utf-8',
      DOUYIN_DATA_DIR: process.env.DOUYIN_DATA_DIR || DATA_DIR,
    },
    stdio: 'ignore',
    detached: false,
    windowsHide: true,
  });
  child.unref();
  return base;
}

/** Make sure the local HTTP API is reachable; start it if allowed. */
export async function ensureServer({
  base = baseUrl(),
  start = true,
  timeoutMs = 12000,
} = {}) {
  if (await probeHealth(base)) return { base, started: false };

  if (!start) {
    throw new Error(`douyin HTTP API 未启动：${base}（可用 --start 自动启动）`);
  }
  spawnServer(base);

  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 400));
    if (await probeHealth(base)) return { base, started: true };
  }
  throw new Error(`douyin HTTP API 启动超时：${base}`);
}

/** One HTTP call; JSON only, no cookie/credential handling. */
export async function request(path, {
  base = baseUrl(),
  method = 'GET',
  body,
  start = true,
  timeoutMs = 180000,
} = {}) {
  await ensureServer({ base, start });
  const res = await fetch(`${base}${path}`, {
    method,
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(timeoutMs),
  });
  const text = await res.text();
  let parsed;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(`douyin HTTP 返回非 JSON（HTTP ${res.status}）：${text.slice(0, 240)}`);
  }
  if (!res.ok || (parsed && typeof parsed === 'object' && parsed.ok === false)) {
    const message = parsed?.error?.message ?? parsed?.error ?? `HTTP ${res.status}`;
    const error = new Error(`douyin HTTP 调用失败：${message}`);
    error.code = parsed?.error?.code ?? parsed?.outcome;
    // 中止的那一页可能还带着已解析的条目；放到 details 里，采集端能捞回来。
    error.details = parsed;
    throw error;
  }
  return parsed;
}

/**
 * One search page → normalized video rows.
 *
 * `offset`/`logid` are optional: leave them out for the first screen (the
 * original behaviour). From the second page on, pass the previous response's
 * `logid` back in — Douyin chains search pages through it (`search_id`), and
 * an offset-only second page usually comes back empty.
 */
export async function searchVideos(keyword, {
  count = 10,
  sort = '综合',
  offset = 0,
  logid = '',
  start = true,
  base,
  requestFn = request,
} = {}) {
  const body = { keyword, count, sort, offset };
  if (logid) body.logid = logid;
  const payload = await requestFn('/search', {
    base,
    method: 'POST',
    body,
    start,
  });
  const page = normalizeSearchPage(payload);
  return {
    keyword,
    count: page.items.length,
    videos: page.items,
    has_more: page.has_more,
    next_offset: page.next_offset,
    logid: page.logid,
    outcome: page.outcome,
    offset: page.offset,
    skipped: page.skipped,
    raw_count: page.raw_count,
  };
}

/** Reasons a multi-page collection can stop. */
export const STOP_REASONS = Object.freeze({
  TARGET_REACHED: 'target_reached',
  NO_MORE: 'no_more',
  EMPTY_PAGE: 'empty_page',
  MISSING_LOGID: 'missing_logid',
  MAX_PAGES: 'max_pages',
  CHALLENGE_REQUIRED: 'challenge_required',
  RATE_LIMITED: 'rate_limited',
  NOT_AUTHENTICATED: 'not_authenticated',
  PARSE_FAILED: 'parse_failed',
  BACKEND_UNAVAILABLE: 'backend_unavailable',
  INVALID_ARGS: 'invalid_args',
});

const ABORT_STOP_REASONS = new Set([
  STOP_REASONS.CHALLENGE_REQUIRED,
  STOP_REASONS.RATE_LIMITED,
  STOP_REASONS.NOT_AUTHENTICATED,
  STOP_REASONS.PARSE_FAILED,
  STOP_REASONS.BACKEND_UNAVAILABLE,
  STOP_REASONS.INVALID_ARGS,
]);

function stopReasonFromError(error, details) {
  const code = String(details?.outcome ?? error?.code ?? '').trim();
  if (ABORT_STOP_REASONS.has(code)) return code;
  if (code === 'not_found') return STOP_REASONS.NO_MORE;
  return STOP_REASONS.BACKEND_UNAVAILABLE;
}

/**
 * Multi-page search collection: fetch pages until the target row count is
 * reached, upstream says "no more", or an aborting error appears.
 *
 * Each page after the first sends the previous page's `logid` as the search
 * credential. Rows are merged and de-duplicated by `aweme_id`, keeping the
 * first occurrence. A failed page never discards pages already collected —
 * the caller gets `videos`, `stop_reason`, and a per-page trace.
 *
 * @param {string} keyword
 * @param {object} options
 * @param {number} options.target      wanted row count (default 20)
 * @param {number} options.count       rows requested per page (default 10)
 * @param {number} options.maxPages    hard page cap (default 10)
 * @param {number} options.delayMs     pause between pages (default 0)
 * @param {Function} options.requestFn injectable for tests; defaults to request()
 */
export async function collectSearchVideos(keyword, {
  target = 20,
  count = 10,
  sort = '综合',
  maxPages = 10,
  delayMs = 0,
  start = true,
  base,
  requestFn = request,
  sleepFn = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
} = {}) {
  const wanted = Math.max(1, Math.round(Number(target) || 20));
  const pageSize = Math.max(1, Math.round(Number(count) || 10));
  const pageCap = Math.max(1, Math.round(Number(maxPages) || 10));

  const merged = [];
  const seen = new Set();
  const pages = [];
  let duplicates = 0;
  let offset = 0;
  let logid = '';
  let stopReason = null;
  let error = null;
  let lastHasMore = null;
  let lastLogid = '';
  let lastNextOffset = null;

  for (let pageNo = 1; pageNo <= pageCap; pageNo += 1) {
    if (pageNo > 1 && delayMs > 0) await sleepFn(delayMs);

    let page;
    try {
      page = await searchVideos(keyword, {
        count: pageSize, sort, offset, logid, start, base, requestFn,
      });
    } catch (err) {
      // Keep every row already collected; record why this page failed.
      const details = err?.details;
      const reason = stopReasonFromError(err, details);
      const salvagedRows = Array.isArray(details?.data?.data) ? details.data.data : [];
      const salvaged = salvagedRows
        .map((row) => normalizeAweme(row?.aweme_info))
        .filter((video) => video?.aweme_id);
      for (const video of salvaged) {
        if (seen.has(video.aweme_id)) { duplicates += 1; continue; }
        seen.add(video.aweme_id);
        merged.push(video);
      }
      stopReason = reason;
      error = {
        code: reason,
        message: String(err?.message ?? err),
        stage: 'page',
      };
      pages.push({
        page: pageNo,
        offset,
        // 这一页请求时带上的是上一页的 logid（首屏为 null）
        logid: logid || null,
        returned_logid: typeof details?.logid === 'string' ? (details.logid || null) : null,
        chained_with_logid: Boolean(logid),
        requested: pageSize,
        count: salvagedRows.length,
        added: salvaged.length,
        items: salvaged.length,
        has_more: details?.has_more ?? null,
        next_offset: details?.next_offset ?? offset,
        outcome: reason,
        error: error.message,
      });
      lastHasMore = details?.has_more ?? null;
      if (Number.isFinite(details?.next_offset)) lastNextOffset = details.next_offset;
      if (typeof details?.logid === 'string' && details.logid) lastLogid = details.logid;
      break;
    }

    const normalized = page.videos;
    let added = 0;
    for (const video of normalized) {
      if (!video?.aweme_id) continue;
      if (seen.has(video.aweme_id)) { duplicates += 1; continue; }
      seen.add(video.aweme_id);
      merged.push(video);
      added += 1;
    }

    const outcome = page.outcome ?? null;
    pages.push({
      page: pageNo,
      offset,
      // 这一页请求时带上的是上一页的 logid（首屏为 null）
      logid: logid || null,
      returned_logid: page.logid,
      chained_with_logid: Boolean(logid),
      requested: pageSize,
      count: normalized.length,
      added,
      items: normalized.length,
      has_more: page.has_more,
      next_offset: page.next_offset,
      outcome,
      error: null,
    });
    lastHasMore = page.has_more;
    lastLogid = page.logid || '';
    lastNextOffset = Number.isFinite(page.next_offset) ? page.next_offset : offset;

    // Upstream asked for a human / throttled us / session died / body broken:
    // stop immediately, never dress this up as "finished".
    if (outcome && ABORT_STOP_REASONS.has(outcome)) {
      stopReason = outcome;
      error = { code: outcome, message: `第 ${pageNo} 页中止：${outcome}`, stage: 'page' };
      break;
    }

    if (merged.length >= wanted) {
      stopReason = STOP_REASONS.TARGET_REACHED;
      break;
    }
    if (page.has_more === false) {
      stopReason = STOP_REASONS.NO_MORE;
      break;
    }
    if (normalized.length === 0) {
      // Empty but not declared "no more": do NOT claim completion.
      stopReason = STOP_REASONS.EMPTY_PAGE;
      break;
    }
    if (!page.logid) {
      // Without the previous page's logid the next request would come back
      // empty; stop and say why instead of burning a request.
      stopReason = STOP_REASONS.MISSING_LOGID;
      break;
    }

    offset = Number.isFinite(page.next_offset) ? page.next_offset : offset + pageSize;
    logid = page.logid;
  }

  if (!stopReason) stopReason = STOP_REASONS.MAX_PAGES;

  // `pages[].logid` 记的是「这一页请求时用掉的凭据」；给调用方续采的是下一页
  // 该用的那个（最后一页成功时返回的 logid），两者别混。
  const nextLogid = stopReason === STOP_REASONS.TARGET_REACHED
      || stopReason === STOP_REASONS.NO_MORE
      || stopReason === STOP_REASONS.MISSING_LOGID
    ? (lastLogid || null)
    : null;

  return {
    keyword,
    sort,
    target: wanted,
    page_size: pageSize,
    max_pages: pageCap,
    count: merged.length,
    videos: merged,
    pages,
    pages_fetched: pages.length,
    duplicates_dropped: duplicates,
    stop_reason: stopReason,
    completed: stopReason === STOP_REASONS.TARGET_REACHED
      || stopReason === STOP_REASONS.NO_MORE,
    partial: merged.length > 0 && ABORT_STOP_REASONS.has(stopReason),
    error,
    has_more: lastHasMore,
    next_offset: lastNextOffset,
    logid: nextLogid,
  };
}

/** Douyin hot list → normalized rows. */
export async function trendingTopics({ start = true, base } = {}) {
  const payload = await request('/trending', { base, method: 'POST', start });
  const rows = Array.isArray(payload?.data) ? payload.data : [];
  return { count: rows.length, items: normalizeHotItems(rows) };
}

/** One video detail. */
export async function videoDetail(id, { start = true, base } = {}) {
  const payload = await request('/detail', {
    base,
    method: 'POST',
    body: { id },
    start,
  });
  return payload;
}

/** Download the no-watermark video; returns local path metadata. */
export async function downloadVideo(id, { start = true, base } = {}) {
  return request('/download', {
    base,
    method: 'POST',
    body: { aweme_id: id },
    start,
  });
}

/** Download + ffmpeg frames; returns local frame paths. */
export async function videoFrames(id, { n = 8, start = true, base } = {}) {
  return request('/frames', {
    base,
    method: 'POST',
    body: { aweme_id: id, n },
    start,
  });
}

/** Creator profile. */
export async function userProfile(user, { start = true, base } = {}) {
  const payload = await request('/profile', {
    base,
    method: 'POST',
    body: { user },
    start,
  });
  const rawUser = payload?.data?.user ?? payload?.data?.author ?? payload?.user ?? null;
  return { user: normalizeUser(rawUser), raw: payload };
}

export { DATA_DIR, ROOT, SERVER_SCRIPT };
