/**
 * lib/search_collect.test.mjs —— 多页搜索采集（collectSearchVideos）的离线自测。
 *
 * 不连真实抖音、不起 Python 服务：把请求函数注入成假上游，只验证
 *   ① 第二页确实带上了第一页的 logid（search_id 链路）；
 *   ② 合并 + aweme_id 去重（保留首次出现）；
 *   ③ 人机验证 / 限流 / 未登录 / 解析失败 → 立刻停且保留已采条目；
 *   ④ 达到目标条数就停，不多请求一页；
 *   ⑤ 空页不当「采完」，缺 logid 不硬翻。
 *
 * 运行：node lib/search_collect.test.mjs
 */
import assert from 'node:assert/strict';

import {
  STOP_REASONS,
  collectSearchVideos,
  searchVideos,
} from './douyin_http_client.mjs';
import { normalizeSearchPage } from './normalize.js';

let pass = 0;
const ok = (name) => { pass += 1; console.log(`[PASS] ${name}`); };

/** 造一页 /search 响应：raw dy 形态（data.data[].aweme_info）。 */
function page(ids, { logid = '', hasMore = null, outcome = 'completed', nextOffset = null } = {}) {
  const rows = ids.map((id) => ({
    aweme_info: {
      aweme_id: String(id),
      desc: `视频 ${id}`,
      author: { nickname: '作者' },
      statistics: { digg_count: Number(id) * 10 },
    },
  }));
  return {
    ok: true,
    outcome,
    has_more: hasMore,
    next_offset: nextOffset ?? ids.length,
    logid,
    data: { data: rows },
  };
}

/**
 * 假上游：pages 是依次要返回的响应（或 Error）。records 记下每次请求体，
 * 用来断言「第二页带了第一页的 logid」。
 */
function fakeUpstream(pages) {
  const records = [];
  let i = 0;
  const requestFn = async (path, { body }) => {
    assert.equal(path, '/search');
    records.push(structuredClone(body));
    const next = pages[i++];
    if (next instanceof Error) throw next;
    return typeof next === 'function' ? next(body) : next;
  };
  return { requestFn, records };
}

/** 模拟 lib/douyin_http_client.mjs 里 request() 抛出的错误形态。 */
function httpError(code, message, details) {
  const err = new Error(message ?? `douyin HTTP 调用失败：${code}`);
  err.code = code;
  err.details = details ?? { ok: false, outcome: code, error: { code, message } };
  return err;
}

try {
  // ── ① 正常两页拼接：条数、去重、页轨迹、logid 链路 ──
  {
    const up = fakeUpstream([
      page([1, 2, 3, 4], { logid: 'LOG-P1', hasMore: true, nextOffset: 4 }),
      page([3, 4, 5, 6], { logid: 'LOG-P2', hasMore: true, nextOffset: 8 }),
    ]);
    const r = await collectSearchVideos('猫', {
      target: 6, count: 4, maxPages: 5, requestFn: up.requestFn,
    });

    assert.equal(r.count, 6);                       // 4 + 4 - 2 重复
    assert.deepEqual(r.videos.map((v) => v.aweme_id),
      ['1', '2', '3', '4', '5', '6']);              // 保留首次出现
    assert.equal(r.duplicates_dropped, 2);
    assert.equal(r.stop_reason, STOP_REASONS.TARGET_REACHED);
    assert.equal(r.pages_fetched, 2);

    // 第二页必须带第一页的 logid，并且 offset 接着上一页的 next_offset
    assert.equal(up.records[0].logid, undefined);   // 首屏不带
    assert.equal(up.records[0].offset, 0);
    assert.equal(up.records[1].logid, 'LOG-P1');
    assert.equal(up.records[1].offset, 4);
    assert.equal(r.pages[1].chained_with_logid, true);
    assert.equal(r.pages[1].logid, 'LOG-P1');
    assert.equal(r.pages[1].returned_logid, 'LOG-P2');
    // 返回给调用方续采的是「下一页该用的凭据」，不是页轨迹里用掉的那个
    assert.equal(r.logid, 'LOG-P2');
    assert.equal(r.next_offset, 8);
    assert.deepEqual(r.pages.map((p) => [p.page, p.offset, p.count, p.logid]),
      [[1, 0, 4, null], [2, 4, 4, 'LOG-P1']]);
    ok('① 两页拼接 + aweme_id 去重 + 第二页带第一页 logid');
  }

  // ── ② 人机验证：立刻停、给 challenge_required、保留已采条目 ──
  {
    const up = fakeUpstream([
      page([11, 12], { logid: 'LOG-A', hasMore: true, nextOffset: 2 }),
      httpError('challenge_required', '抖音搜索返回 verify_check：需要人机验证'),
    ]);
    const r = await collectSearchVideos('猫', {
      target: 10, count: 2, maxPages: 5, requestFn: up.requestFn,
    });

    assert.deepEqual(r.videos.map((v) => v.aweme_id), ['11', '12']);
    assert.equal(r.count, 2);
    assert.equal(r.stop_reason, STOP_REASONS.CHALLENGE_REQUIRED);
    assert.equal(r.partial, true);
    assert.equal(r.completed, false);
    assert.equal(r.pages_fetched, 2);
    assert.match(r.error.message, /verify_check/);
    // 中止后不得再请求第三页
    assert.equal(up.records.length, 2);
    ok('② verify_check → challenge_required，保留已采条目且不再请求');
  }

  // ── ③ 目标条数第一页就满足：只请求一页，不多打一次 ──
  {
    const up = fakeUpstream([
      page([21, 22, 23], { logid: 'LOG-1', hasMore: true, nextOffset: 3 }),
      page([31], { logid: 'LOG-2', hasMore: true }),
    ]);
    const r = await collectSearchVideos('猫', {
      target: 3, count: 5, maxPages: 5, requestFn: up.requestFn,
    });
    assert.equal(r.count, 3);
    assert.equal(r.stop_reason, STOP_REASONS.TARGET_REACHED);
    assert.equal(up.records.length, 1);
    assert.equal(r.pages_fetched, 1);
    ok('③ 目标条数首屏即满足 → 只请求一页');
  }

  // ── ④ 上游明确 no_more ──
  {
    const up = fakeUpstream([
      page([41, 42], { logid: 'LOG-N', hasMore: false, nextOffset: 2 }),
    ]);
    const r = await collectSearchVideos('猫', {
      target: 10, count: 2, maxPages: 5, requestFn: up.requestFn,
    });
    assert.equal(r.count, 2);
    assert.equal(r.stop_reason, STOP_REASONS.NO_MORE);
    assert.equal(r.completed, true);
    assert.equal(up.records.length, 1);
    ok('④ has_more=false → no_more，一次请求即止');
  }

  // ── ⑤ 空页但没说 no_more：不得冒充「已采完」 ──
  {
    const up = fakeUpstream([
      page([51], { logid: 'LOG-E', hasMore: true, nextOffset: 1 }),
      page([], { logid: 'LOG-E2', hasMore: true, nextOffset: 1 }),
    ]);
    const r = await collectSearchVideos('猫', {
      target: 10, count: 1, maxPages: 5, requestFn: up.requestFn,
    });
    assert.equal(r.stop_reason, STOP_REASONS.EMPTY_PAGE);
    assert.equal(r.completed, false);
    assert.equal(r.count, 1);                        // 第一页的条目还在
    assert.equal(up.records.length, 2);
    ok('⑤ 空页 → empty_page，保留已有条目且不标完成');
  }

  // ── ⑥ 首页就没有 logid：不硬翻，明说缺凭据 ──
  {
    const up = fakeUpstream([
      page([61, 62], { logid: '', hasMore: true, nextOffset: 2 }),
    ]);
    const r = await collectSearchVideos('猫', {
      target: 10, count: 2, maxPages: 5, requestFn: up.requestFn,
    });
    assert.equal(r.stop_reason, STOP_REASONS.MISSING_LOGID);
    assert.equal(up.records.length, 1);
    assert.equal(r.count, 2);
    ok('⑥ 无 logid → missing_logid，没有无效翻页');
  }

  // ── ⑦ 各种中止错误码原样透出 ──
  for (const [code, expected] of [
    ['rate_limited', STOP_REASONS.RATE_LIMITED],
    ['not_authenticated', STOP_REASONS.NOT_AUTHENTICATED],
    ['parse_failed', STOP_REASONS.PARSE_FAILED],
    ['backend_unavailable', STOP_REASONS.BACKEND_UNAVAILABLE],
  ]) {
    const up = fakeUpstream([
      page([71], { logid: 'LOG-7', hasMore: true, nextOffset: 1 }),
      httpError(code),
    ]);
    const r = await collectSearchVideos('猫', {
      target: 10, count: 1, maxPages: 5, requestFn: up.requestFn,
    });
    assert.equal(r.stop_reason, expected, code);
    assert.equal(r.count, 1, code);
    assert.equal(r.videos[0].aweme_id, '71');
  }
  ok('⑦ rate_limited / not_authenticated / parse_failed / backend_unavailable 都保留已采数据');

  // ── ⑧ 中止页自带条目时也要捞回来（不丢已拿到的条目） ──
  {
    const up = fakeUpstream([
      page([81], { logid: 'LOG-8', hasMore: true, nextOffset: 1 }),
      httpError('challenge_required', 'verify_check', {
        ok: false,
        outcome: 'challenge_required',
        next_offset: 2,
        has_more: null,
        data: { data: [{ aweme_info: { aweme_id: '82', desc: '第二条' } }] },
      }),
    ]);
    const r = await collectSearchVideos('猫', {
      target: 10, count: 1, maxPages: 5, requestFn: up.requestFn,
    });
    assert.deepEqual(r.videos.map((v) => v.aweme_id), ['81', '82']);
    assert.equal(r.stop_reason, STOP_REASONS.CHALLENGE_REQUIRED);
    ok('⑧ 中止页里已解析出的条目会被合并进结果');
  }

  // ── ⑨ maxPages 封顶 ──
  {
    const up = fakeUpstream([
      page([91], { logid: 'L1', hasMore: true, nextOffset: 1 }),
      page([92], { logid: 'L2', hasMore: true, nextOffset: 2 }),
      page([93], { logid: 'L3', hasMore: true, nextOffset: 3 }),
    ]);
    const r = await collectSearchVideos('猫', {
      target: 100, count: 1, maxPages: 3, requestFn: up.requestFn,
    });
    assert.equal(up.records.length, 3);
    assert.equal(r.stop_reason, STOP_REASONS.MAX_PAGES);
    assert.equal(r.count, 3);
    ok('⑨ maxPages 封顶且链式 logid 逐页推进');
  }

  // ── ⑩ 单页 searchVideos：outcome/next_offset/logid 透出 ──
  {
    const up = fakeUpstream([page([101], { logid: 'SINGLE-LOG', hasMore: null, nextOffset: 1 })]);
    const r = await searchVideos('猫', { count: 1, requestFn: up.requestFn });
    assert.equal(r.count, 1);
    assert.equal(r.logid, 'SINGLE-LOG');
    assert.equal(r.next_offset, 1);
    assert.equal(r.has_more, null);                  // 三态：上游没说就是 null
    assert.equal(up.records[0].logid, undefined);
    ok('⑩ searchVideos 首屏行为不变（不传 logid 即不带字段）');
  }

  // ── ⑪ normalizer：缺字段要 null，不猜 ──
  {
    const p = normalizeSearchPage({
      ok: true, outcome: 'completed', logid: '', data: { data: [] },
    });
    assert.equal(p.logid, null);
    assert.equal(p.has_more, null);
    assert.equal(p.next_offset, null);
    assert.equal(p.items.length, 0);
    ok('⑪ normalizeSearchPage 稀疏字段保持 null');
  }

  // ── ⑫ 页间停顿注入（默认 0，采集链路可配置） ──
  {
    const up = fakeUpstream([
      page([111], { logid: 'L-1', hasMore: true, nextOffset: 1 }),
      page([112], { logid: 'L-2', hasMore: true, nextOffset: 2 }),
    ]);
    const slept = [];
    const r = await collectSearchVideos('猫', {
      target: 10, count: 1, maxPages: 3, delayMs: 25,
      sleepFn: async (ms) => { slept.push(ms); },
      requestFn: up.requestFn,
    });
    // 首屏不等；之后每请求一页之前等一次（这里 3 页 → 2 次停顿）
    assert.deepEqual(slept, [25, 25]);
    assert.equal(r.count, 2);
    assert.equal(up.records.length, 3);
    ok('⑫ 页间停顿只在翻页时发生');
  }

  // ── ⑬ details.outcome 优先于 error.code（服务端 request() 的真实形态） ──
  {
    const err = new Error('douyin HTTP 调用失败：搜索中止：challenge_required');
    err.code = 'challenge_required';            // request() 从 error.code ?? outcome 取
    err.details = { ok: false, outcome: 'challenge_required', error: { code: 'challenge_required' } };
    const up = fakeUpstream([page([121], { logid: 'L', hasMore: true, nextOffset: 1 }), err]);
    const r = await collectSearchVideos('猫', {
      target: 10, count: 1, maxPages: 5, requestFn: up.requestFn,
    });
    assert.equal(r.stop_reason, STOP_REASONS.CHALLENGE_REQUIRED);
    assert.equal(r.count, 1);

    // 只有 details.outcome 也认
    const err2 = new Error('boom');
    err2.details = { ok: false, outcome: 'rate_limited' };
    const up2 = fakeUpstream([page([122], { logid: 'L', hasMore: true, nextOffset: 1 }), err2]);
    const r2 = await collectSearchVideos('猫', {
      target: 10, count: 1, maxPages: 5, requestFn: up2.requestFn,
    });
    assert.equal(r2.stop_reason, STOP_REASONS.RATE_LIMITED);
    ok('⑬ 错误体里的 outcome 优先于 error.code');
  }

  console.log(`\nALL PASSED (${pass})`);
} catch (err) {
  console.error(err);
  process.exitCode = 1;
}
