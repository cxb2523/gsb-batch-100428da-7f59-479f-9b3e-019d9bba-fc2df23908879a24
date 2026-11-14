/**
 * VietFuel API
 * Copyright (c) 2026 TranQui
 * Github: https://github.com/TranQui004
 *
 * Licensed under the MIT License.
 * See LICENSE file for details.
 */

/* ==========================================================================
 * [TEST] - PVOil provenance & completeness (offline, không cần mạng)
 * Mục đích: Xác minh scrapePVOil gắn đúng metadata nguồn (tier, sourceUrl,
 * isDegradedSource) và độ đầy đủ (expectedProducts, missingProducts,
 * isComplete) cho từng tầng, dùng dữ liệu mẫu dựng sẵn thay vì gọi mạng thật.
 * ========================================================================== */

import assert from 'assert';
import { fileURLToPath } from 'url';
import {
  scrapePVOil,
  assessPvoilCompleteness,
  TIER_INFO,
} from '../../src/scrapers/pvoil.js';
import { OFFICIAL_PRODUCT_NAMES } from '../../src/scrapers/pvoil-parser.js';

// ── Dữ liệu mẫu dựng sẵn (giả lập kết quả parse của từng tầng) ─────────────
function makePrice(name, price) {
  return { name, region1: null, region2: null, price, unit: 'VND/lít' };
}

// Đủ 5 sản phẩm niêm yết chính thức của PVOil
const FULL_PRICES = [
  makePrice('Xăng RON 95-III', 21500),
  makePrice('Xăng E10 RON 95-III', 21200),
  makePrice('Xăng E5 RON 92-II', 20400),
  makePrice('DO 0,05S-II', 19300),
  makePrice('DO 0,001S-V', 19800),
];

// Giống dữ liệu thực tế của GXHN: thiếu Xăng E10 RON 95-III
const GXHN_PRICES = FULL_PRICES.filter((p) => p.name !== 'Xăng E10 RON 95-III');

// Giống dữ liệu tham chiếu Petrolimex: chỉ còn 2 sản phẩm trùng
const PETROTIMES_PRICES = [
  makePrice('Xăng RON 95-III', 21500),
  makePrice('DO 0,05S-II', 19300),
];

function makeTierResult(prices, overrides = {}) {
  return {
    prices,
    scrapedAt: '2026-11-15T08:00:00.000Z',
    source: 'https://www.pvoil.com.vn',
    priceDate: '2026-11-14',
    priceDateSource: 'test-fixture',
    priceAnnouncedAt: null,
    ...overrides,
  };
}

const failingTier = (label) => async () => { throw new Error(`${label} failed (fixture)`); };

async function run() {
  const startedAt = Date.now();

  // ── 0. Danh mục chính thức từ parser phải đúng 5 sản phẩm ───────────────
  assert.deepStrictEqual(
    OFFICIAL_PRODUCT_NAMES,
    ['Xăng RON 95-III', 'Xăng E10 RON 95-III', 'Xăng E5 RON 92-II', 'DO 0,05S-II', 'DO 0,001S-V'],
    'OFFICIAL_PRODUCT_NAMES phải là 5 sản phẩm bán lẻ chính thức',
  );

  // ── 1. Tầng 0 trúng, đủ 5 sản phẩm ──────────────────────────────────────
  {
    const calls = [];
    const result = await scrapePVOil({
      tierScrapers: [
        async () => { calls.push(0); return makeTierResult(FULL_PRICES, { sourceUrl: 'https://103.21.120.100/api/oilprice/load-view' }); },
        async () => { calls.push(1); return makeTierResult(FULL_PRICES); },
      ],
    });

    assert.deepStrictEqual(calls, [0], 'phải dừng ngay ở tầng 0 khi thành công');
    assert.strictEqual(result.tier, 0);
    assert.strictEqual(result._tier, 0, 'giữ _tier tương thích ngược');
    assert.strictEqual(result.tierKey, TIER_INFO[0].key);
    assert.strictEqual(result.sourceUrl, 'https://103.21.120.100/api/oilprice/load-view', 'sourceUrl do tầng ghi phải được giữ nguyên');
    assert.strictEqual(result.isDegradedSource, false, 'tầng 0 là nguồn PVOil, không phải hạ cấp');
    assert.strictEqual(result.blockedByProtection, undefined, 'tầng 0 không set blockedByProtection');
    assert.deepStrictEqual(result.missingProducts, []);
    assert.strictEqual(result.isComplete, true);
    assert.deepStrictEqual(result.expectedProducts, OFFICIAL_PRODUCT_NAMES);
    // Các field hiện hữu không đổi
    assert.strictEqual(result.prices.length, 5);
    assert.strictEqual(result.source, 'https://www.pvoil.com.vn');
    assert.strictEqual(result.scrapedAt, '2026-11-15T08:00:00.000Z');
    assert.strictEqual(result.priceDate, '2026-11-14');
  }

  // ── 2. Tầng 0,1 lỗi → tầng 2 (GXHN) trúng, thiếu E10 ────────────────────
  {
    const gxhnUrl = 'https://giaxanghomnay.com/api/pvdate/2026-11-14';
    const result = await scrapePVOil({
      tierScrapers: [
        failingTier('tier0'),
        failingTier('tier1'),
        async () => makeTierResult(GXHN_PRICES, { source: 'https://giaxanghomnay.com', sourceUrl: gxhnUrl }),
        failingTier('tier3'),
      ],
    });

    assert.strictEqual(result.tier, 2);
    assert.strictEqual(result.tierKey, 'gxhn-json-api');
    assert.strictEqual(result.sourceUrl, gxhnUrl);
    assert.strictEqual(result.isDegradedSource, true, 'tầng 2 phải bị đánh dấu nguồn hạ cấp');
    assert.strictEqual(result.blockedByProtection, true, 'giữ hành vi blockedByProtection hiện hữu');
    assert.deepStrictEqual(result.missingProducts, ['Xăng E10 RON 95-III']);
    assert.strictEqual(result.isComplete, true, '4/5 sản phẩm vẫn đủ tin cậy');
    assert.strictEqual(result.source, 'https://giaxanghomnay.com', 'field source hiện hữu không đổi');
  }

  // ── 3. Chỉ tầng 3 trúng với 2 sản phẩm → đánh dấu không đầy đủ ──────────
  {
    const result = await scrapePVOil({
      tierScrapers: [
        failingTier('tier0'),
        failingTier('tier1'),
        failingTier('tier2'),
        async () => makeTierResult(PETROTIMES_PRICES, { sourceUrl: 'https://petrotimesgroup.com/site/get-petro' }),
      ],
    });

    assert.strictEqual(result.tier, 3);
    assert.strictEqual(result.isDegradedSource, true, 'tầng 3 phải bị đánh dấu nguồn hạ cấp');
    assert.strictEqual(result.blockedByProtection, true);
    assert.deepStrictEqual(
      result.missingProducts,
      ['Xăng E10 RON 95-III', 'Xăng E5 RON 92-II', 'DO 0,001S-V'],
    );
    assert.strictEqual(result.isComplete, false, 'thiếu quá nửa danh mục phải bị đánh dấu incomplete');
  }

  // ── 4. sourceUrl mặc định theo tầng khi tier function không ghi ──────────
  {
    const result = await scrapePVOil({
      tierScrapers: [async () => makeTierResult(FULL_PRICES)],
    });
    assert.strictEqual(result.sourceUrl, TIER_INFO[0].sourceUrl, 'fallback sourceUrl theo TIER_INFO');
  }

  // ── 5. Cả 4 tầng đều lỗi → phải throw, không trả kết quả rỗng ───────────
  {
    let threw = null;
    try {
      await scrapePVOil({
        tierScrapers: [failingTier('t0'), failingTier('t1'), failingTier('t2'), failingTier('t3')],
      });
    } catch (e) { threw = e; }
    assert(threw, 'phải throw khi tất cả tầng thất bại');
    assert(
      threw.message.includes('4 tầng') || threw.message.includes('4 t'),
      `thông điệp lỗi phải giữ nguyên hành vi cũ, nhận được: ${threw.message}`,
    );
  }

  // ── 6. assessPvoilCompleteness: unit test trực tiếp ─────────────────────
  {
    const empty = assessPvoilCompleteness([]);
    assert.deepStrictEqual(empty.missingProducts, OFFICIAL_PRODUCT_NAMES);
    assert.strictEqual(empty.isComplete, false);

    const full = assessPvoilCompleteness(FULL_PRICES);
    assert.deepStrictEqual(full.missingProducts, []);
    assert.strictEqual(full.isComplete, true);

    const three = assessPvoilCompleteness(FULL_PRICES.slice(0, 3));
    assert.strictEqual(three.isComplete, true, '3/5 sản phẩm là ngưỡng tối thiểu đủ tin cậy');
    assert.deepStrictEqual(three.missingProducts, ['DO 0,05S-II', 'DO 0,001S-V']);

    const junk = assessPvoilCompleteness(null);
    assert.strictEqual(junk.isComplete, false, 'input không hợp lệ phải an toàn');
  }

  return {
    name: 'scrapers/pvoil-provenance.smoke',
    ok: true,
    count: 6,
    durationMs: Date.now() - startedAt,
  };
}

export { run };

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  run()
    .then((summary) => {
      console.log(`[PASS] ${summary.name} - ${summary.count} cases (${summary.durationMs}ms)`);
      process.exit(0);
    })
    .catch((err) => {
      console.error('[FAIL] scrapers/pvoil-provenance.smoke:', err);
      process.exit(1);
    });
}
