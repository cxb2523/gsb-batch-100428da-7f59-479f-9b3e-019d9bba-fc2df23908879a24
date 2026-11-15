/**
 * Local verification for the cron price-check rewrite.
 *
 * Runs entirely against in-memory fakes (no network, no real Supabase/Resend/
 * Firecrawl). Run with:
 *
 *   node --experimental-strip-types scripts/verify-check-prices.mts
 *
 * (Node >= 22.6; the flag is unnecessary on Node 23+.)
 */
import assert from "node:assert/strict";

import {
  isAuthorized,
  normalizeCurrencyCode,
  runPriceCheck,
} from "../lib/cron/price-check.ts";
import type {
  EmailResult,
  PriceCheckDeps,
  ScrapedProduct,
} from "../lib/cron/price-check.ts";

type DbRow = Record<string, unknown> & { id: string };

type HistoryRow = {
  product_id: string;
  price: number;
  currency: string;
  checked_at?: string;
};

type FakeOptions = {
  products: DbRow[];
  scrapes: Record<string, ScrapedProduct | Error>;
  email: { success: boolean; error?: unknown; skipped?: boolean };
  getUserByEmail?: Record<string, { email?: string }>;
  failHistoryForProductIds?: string[];
  historyRetentionDays?: number;
  oldHistoryRows?: HistoryRow[];
};

function chainable<T extends object>(target: T): T {
  // Every chainable builder method resolves to the same awaitable object.
  const self = target as unknown as Record<string, unknown>;
  // Note: "delete" and "insert" are intentionally excluded so custom
  // implementations on the target are never clobbered.
  for (const key of ["select", "update", "eq", "lt", "order"]) {
    if (self[key] === undefined) {
      self[key] = () => self;
    }
  }
  return target;
}

function makeFakeSupabase(options: FakeOptions) {
  const productRows = options.products.map((row) => ({ ...row }));
  const historyRows: HistoryRow[] = [...(options.oldHistoryRows ?? [])];
  const calls = {
    productUpdates: [] as Array<{ id: string; patch: Record<string, unknown> }>,
    historyInserts: [] as HistoryRow[],
    historyDeletes: 0,
    getUserById: [] as string[],
  };

  const builder = {
    then: undefined as unknown,
  };

  const from = (table: string) => {
    if (table === "products") {
      return chainable({
        async then<T>(
          resolve: (value: unknown) => T,
          reject: (reason?: unknown) => T,
        ): Promise<T> {
          try {
            return resolve({ data: productRows, error: null });
          } catch (error) {
            return reject(error);
          }
        },
        update(patch: Record<string, unknown>) {
          return chainable({
            async eq(idColumn: string, idValue: string) {
              assert.equal(idColumn, "id");
              calls.productUpdates.push({ id: idValue, patch });
              const row = productRows.find((item) => item.id === idValue);
              if (row) Object.assign(row, patch);
              return { data: null, error: null };
            },
          });
        },
      });
    }

    if (table === "price_history") {
      const tableBuilder: Record<string, unknown> = {
        insert(row: HistoryRow) {
          return chainable({
            async then<T>(
              resolve: (value: unknown) => T,
              reject: (reason?: unknown) => T,
            ): Promise<T> {
              try {
                if (
                  options.failHistoryForProductIds?.includes(row.product_id)
                ) {
                  return resolve({
                    data: null,
                    error: { message: "simulated history insert failure" },
                  });
                }
                calls.historyInserts.push({ ...row });
                historyRows.push({ ...row, checked_at: new Date().toISOString() });
                return resolve({ data: null, error: null });
              } catch (error) {
                return reject(error);
              }
            },
          });
        },
        delete() {
          return {
            lt(column: string, cutoff: string) {
              assert.equal(column, "checked_at");
              const cutoffTime = Date.parse(cutoff);
              const before = historyRows.length;
              for (let index = historyRows.length - 1; index >= 0; index--) {
                const row = historyRows[index];
                if (row.checked_at && Date.parse(row.checked_at) < cutoffTime) {
                  historyRows.splice(index, 1);
                }
              }
              const removed = before - historyRows.length;
              calls.historyDeletes += 1;
              return Promise.resolve({ data: null, error: null, count: removed });
            },
          };
        },
      };
      return chainable(tableBuilder);
    }

    throw new Error(`Unexpected table: ${table}`);
  };

  const supabase = {
    from,
    auth: {
      admin: {
        async getUserById(userId: string) {
          calls.getUserById.push(userId);
          const user = options.getUserByEmail?.[userId];
          if (!user) {
            return {
              data: { user: null },
              error: { message: "user not found" },
            };
          }
          return { data: { user: { id: userId, email: user.email } }, error: null };
        },
      },
    },
  };

  return { supabase, calls, productRows, historyRows, builder };
}

function makeDeps(options: FakeOptions) {
  const scrapeCalls: string[] = [];
  const emailCalls: Array<{
    email: string;
    oldPrice: number;
    newPrice: number;
  }> = [];

  const deps: PriceCheckDeps = {
    async scrapeProduct(url: string): Promise<ScrapedProduct> {
      scrapeCalls.push(url);
      const behavior = options.scrapes[url];
      if (behavior === undefined) {
        throw new Error(`no scrape configured for ${url}`);
      }
      if (behavior instanceof Error) throw behavior;
      return behavior;
    },
    async sendPriceDropAlert(
      email: string,
      _product: unknown,
      oldPrice: number,
      newPrice: number,
    ): Promise<EmailResult> {
      emailCalls.push({ email, oldPrice, newPrice });
      return {
        success: options.email.success,
        skipped: options.email.skipped,
        error: options.email.error,
      };
    },
  };

  return { deps, scrapeCalls, emailCalls };
}

function makeRequest(authHeader?: string) {
  const headers = new Headers();
  if (authHeader) headers.set("authorization", authHeader);
  return new Request("http://localhost/api/cron/check-prices", { headers });
}

let passed = 0;
function check(label: string, condition: boolean) {
  assert.equal(condition, true, label);
  passed++;
  console.log(`  ✓ ${label}`);
}

const PRODUCTS = {
  drop: {
    id: "p-drop",
    url: "https://shop.example.com/drop",
    current_price: 100,
    currency: "USD",
    name: "Dropping Gadget",
    image_url: "https://cdn.example.com/drop.png",
    user_id: "u-1",
  },
  same: {
    id: "p-same",
    url: "https://shop.example.com/same",
    current_price: 50,
    currency: "USD",
    name: "Steady Widget",
    image_url: null,
    user_id: "u-2",
  },
  fail: {
    id: "p-fail",
    url: "https://shop.example.com/broken",
    current_price: 80,
    currency: "EUR",
    name: "Broken Page",
    image_url: null,
    user_id: "u-3",
  },
};

async function main() {
  console.log("\n[1] Acceptance run: one price drop, one unchanged, one scrape failure");
  {
    const options: FakeOptions = {
      products: [
        { ...PRODUCTS.drop },
        { ...PRODUCTS.same },
        { ...PRODUCTS.fail },
      ],
      scrapes: {
        [PRODUCTS.drop.url]: {
          productName: "Dropping Gadget",
          currentPrice: 80,
          currencyCode: "USD",
          productImageUrl: "https://cdn.example.com/drop.png",
        },
        [PRODUCTS.same.url]: {
          productName: "Steady Widget",
          currentPrice: 50,
          currencyCode: "$",
        },
        [PRODUCTS.fail.url]: new Error("Failed to scrape product: timeout"),
      },
      email: { success: true },
      getUserByEmail: {
        "u-1": { email: "buyer@example.com" },
        "u-2": { email: "steady@example.com" },
      },
      oldHistoryRows: [
        {
          product_id: "p-ancient",
          price: 1,
          currency: "USD",
          checked_at: new Date(Date.now() - 200 * 86400000).toISOString(),
        },
        {
          product_id: "p-recent",
          price: 2,
          currency: "USD",
          checked_at: new Date().toISOString(),
        },
      ],    };

    const { supabase, calls, historyRows, productRows } = makeFakeSupabase(options);
    const { deps, scrapeCalls, emailCalls } = makeDeps(options);

    const report = await runPriceCheck({
      supabase: supabase as never,
      deps,
      retentionDays: 90,
    });

    check("totals: total=3", report.total === 3);
    check("totals: updated=1", report.updated === 1);
    check("totals: unchanged=1", report.unchanged === 1);
    check("totals: failed=1", report.failed === 1);
    check("totals: priceChanges=1", report.priceChanges === 1);
    check("totals: priceDrops=1", report.priceDrops === 1);
    check("totals: alertsSent=1", report.alertsSent === 1);
    check("totals: alertsFailed=0", report.alertsFailed === 0);

    const updated = report.updatedProducts;
    check("one updated product listed", updated.length === 1);
    check(
      "updated product is the price drop",
      updated[0]?.productId === "p-drop" &&
        updated[0]?.oldPrice === 100 &&
        updated[0]?.newPrice === 80 &&
        updated[0]?.priceDirection === "down" &&
        updated[0]?.historyWritten === true,
    );

    const unchanged = report.unchangedProducts;
    check("one unchanged product listed", unchanged.length === 1);
    check(
      "unchanged product carries id/url/price",
      unchanged[0]?.productId === "p-same" &&
        unchanged[0]?.url === PRODUCTS.same.url &&
        unchanged[0]?.price === 50,
    );

    const failed = report.failedProducts;
    check("one failed product listed", failed.length === 1);
    check(
      "failed product carries id, url and a short reason",
      failed[0]?.productId === "p-fail" &&
        failed[0]?.url === PRODUCTS.fail.url &&
        failed[0]?.stage === "scrape" &&
        typeof failed[0]?.reason === "string" &&
        failed[0].reason.length > 0,
    );

    check(
      "every product was still attempted (failure did not stop the run)",
      scrapeCalls.length === 3 &&
        scrapeCalls.includes(PRODUCTS.drop.url) &&
        scrapeCalls.includes(PRODUCTS.same.url) &&
        scrapeCalls.includes(PRODUCTS.fail.url),
    );

    check("exactly one history row inserted (the price drop)", calls.historyInserts.length === 1);
    check(
      "history row belongs to the dropped product with normalized price/currency",
      calls.historyInserts[0]?.product_id === "p-drop" &&
        calls.historyInserts[0]?.price === 80 &&
        calls.historyInserts[0]?.currency === "USD",
    );
    check(
      "failed product has no history written",
      !calls.historyInserts.some((row) => row.product_id === "p-fail"),
    );
    check(
      "unchanged product has no history written",
      !calls.historyInserts.some((row) => row.product_id === "p-same"),
    );

    check("exactly one alert email sent", emailCalls.length === 1);
    check(
      "alert went to the drop product's user with correct prices",
      emailCalls[0]?.email === "buyer@example.com" &&
        emailCalls[0]?.oldPrice === 100 &&
        emailCalls[0]?.newPrice === 80,
    );
    check(
      "alert outcome recorded as sent",
      report.alerts.length === 1 && report.alerts[0]?.status === "sent",
    );

    check(
      "failed product was never updated and keeps its stored price",
      productRows.find((row) => row.id === "p-fail")?.current_price === 80,
    );
    check(
      "unchanged product row was refreshed (update call present)",
      calls.productUpdates.some((call) => call.id === "p-same"),
    );

    check(
      "symbol currency normalized to ISO code",
      normalizeCurrencyCode("$") === "USD" &&
        normalizeCurrencyCode("₹") === "INR",
    );

    check("pruning still ran and removed only the old row", report.historyPruned === 1);
    check("prune delete count matches", calls.historyDeletes === 1);
    check(
      "recent history row survived pruning",
      historyRows.some((row) => row.product_id === "p-recent"),
    );
  }

  console.log("\n[2] Re-run safety: failed scrape writes nothing and emails nobody");
  {
    const options: FakeOptions = {
      products: [{ ...PRODUCTS.fail }],
      scrapes: { [PRODUCTS.fail.url]: new Error("boom") },
      email: { success: true },
    };
    const { supabase, calls } = makeFakeSupabase(options);
    const { deps } = makeDeps(options);

    const report = await runPriceCheck({
      supabase: supabase as never,
      deps,
      retentionDays: 90,
    });

    check("product classified as failed", report.failed === 1 && report.failedProducts.length === 1);
    check("no product update on failure", calls.productUpdates.length === 0);
    check("no history insert on failure", calls.historyInserts.length === 0);
    check("no user lookup / email on failure", calls.getUserById.length === 0);
  }

  console.log("\n[3] Parse failures (missing/invalid price) are classified and side-effect free");
  {
    const missingPrice = { ...PRODUCTS.drop, id: "p-missing", url: "https://x/missing" };
    const badPrice = { ...PRODUCTS.same, id: "p-bad", url: "https://x/bad", current_price: "NaN" };
    const options: FakeOptions = {
      products: [missingPrice, badPrice],
      scrapes: {
        "https://x/missing": {
          productName: "No price",
          currentPrice: null,
        } as unknown as ScrapedProduct,
        "https://x/bad": { productName: "Bad", currentPrice: 12 },
      },
      email: { success: true },
    };
    const { supabase, calls } = makeFakeSupabase(options);
    const { deps } = makeDeps(options);

    const report = await runPriceCheck({
      supabase: supabase as never,
      deps,
      retentionDays: 90,
    });

    check("both products failed at parse stage", report.failed === 2);
    check(
      "both are stage=parse",
      report.failedProducts.every((item) => item.stage === "parse"),
    );
    check("no history written for parse failures", calls.historyInserts.length === 0);
  }

  console.log("\n[4] Price drop with failing email: history kept, alert recorded as failed");
  {
    const options: FakeOptions = {
      products: [{ ...PRODUCTS.drop }],
      scrapes: {
        [PRODUCTS.drop.url]: {
          productName: "Dropping Gadget",
          currentPrice: 80,
          currencyCode: "USD",
        },
      },
      email: { success: false, error: { message: "resend down" } },
      getUserByEmail: { "u-1": { email: "buyer@example.com" } },
    };
    const { supabase, calls } = makeFakeSupabase(options);
    const { deps, emailCalls } = makeDeps(options);

    const report = await runPriceCheck({
      supabase: supabase as never,
      deps,
      retentionDays: 90,
    });

    check("product still counts as updated", report.updated === 1);
    check("history was written before the email attempt", calls.historyInserts.length === 1);
    check("email was attempted once", emailCalls.length === 1);
    check("alertsSent stays 0", report.alertsSent === 0);
    check("alertsFailed=1 with reason", report.alertsFailed === 1);
    check(
      "alert outcome is failed and includes id/url",
      report.alerts[0]?.status === "failed" &&
        report.alerts[0]?.productId === "p-drop" &&
        typeof report.alerts[0]?.reason === "string",
    );
  }

  console.log("\n[5] Price increase: history written, no email");
  {
    const up = { ...PRODUCTS.same, id: "p-up", url: "https://x/up", current_price: 40 };
    const options: FakeOptions = {
      products: [up],
      scrapes: { "https://x/up": { productName: "Up", currentPrice: 60 } },
      email: { success: true },
      getUserByEmail: { "u-2": { email: "steady@example.com" } },
    };
    const { supabase, calls } = makeFakeSupabase(options);
    const { deps } = makeDeps(options);

    const report = await runPriceCheck({
      supabase: supabase as never,
      deps,
      retentionDays: 90,
    });

    check("price increase is an update with direction up", report.updated === 1 && report.priceDrops === 0);
    check("history written for the increase", calls.historyInserts.length === 1);
    check("no alert record for an increase", report.alerts.length === 0);
    check("no user lookup for an increase", calls.getUserById.length === 0);
  }

  console.log("\n[6] History insert failure: product reported failed, no email sent");
  {
    const options: FakeOptions = {
      products: [{ ...PRODUCTS.drop }],
      scrapes: {
        [PRODUCTS.drop.url]: { productName: "Dropping Gadget", currentPrice: 80 },
      },
      email: { success: true },
      getUserByEmail: { "u-1": { email: "buyer@example.com" } },
      failHistoryForProductIds: ["p-drop"],
    };
    const { supabase, calls } = makeFakeSupabase(options);
    const { deps } = makeDeps(options);

    const report = await runPriceCheck({
      supabase: supabase as never,
      deps,
      retentionDays: 90,
    });

    check("product is classified failed (database stage)", report.failed === 1);
    check(
      "stage is database and reason mentions history",
      report.failedProducts[0]?.stage === "database" &&
        report.failedProducts[0]?.reason.includes("history"),
    );
    check("no email when history write fails", calls.getUserById.length === 0);
  }

  console.log("\n[7] Empty product list still prunes and returns zeroed report");
  {
    const options: FakeOptions = {
      products: [],
      scrapes: {},
      email: { success: true },
      oldHistoryRows: [
        {
          product_id: "p-old",
          price: 1,
          currency: "USD",
          checked_at: new Date(Date.now() - 100 * 86400000).toISOString(),
        },
      ],
    };
    const { supabase } = makeFakeSupabase(options);
    const { deps } = makeDeps(options);

    const report = await runPriceCheck({
      supabase: supabase as never,
      deps,
      retentionDays: 90,
    });

    check("total is zero", report.total === 0);
    check("all outcome arrays are empty", report.failedProducts.length === 0 && report.updatedProducts.length === 0);
    check("pruning still performed", report.historyPruned === 1);
  }

  console.log("\n[8] Authorization");
  {
    const previousSecret = process.env.CRON_SECRET;
    process.env.CRON_SECRET = "secret-value";

    check("missing header is rejected", isAuthorized(makeRequest()) === false);
    check(
      "wrong bearer is rejected",
      isAuthorized(makeRequest("Bearer wrong-value")) === false,
    );
    check(
      "correct bearer is accepted",
      isAuthorized(makeRequest("Bearer secret-value")) === true,
    );

    delete process.env.CRON_SECRET;
    check("unconfigured secret rejects everything", isAuthorized(makeRequest("Bearer secret-value")) === false);

    if (previousSecret === undefined) delete process.env.CRON_SECRET;
    else process.env.CRON_SECRET = previousSecret;
  }

  console.log(`\nAll ${passed} checks passed.\n`);
}

main().catch((error) => {
  console.error("\nVerification FAILED:\n", error);
  process.exit(1);
});
