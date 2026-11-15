import type { SupabaseClient } from "@supabase/supabase-js";

/**
 * Pure cron price-check logic.
 *
 * This module must stay free of runtime imports from next/server, resend,
 * firecrawl etc. All side-effecting collaborators are injected through
 * {@link PriceCheckDeps} so the whole run can be exercised locally with
 * in-memory fakes (see scripts/verify-check-prices.ts).
 */

export type ScrapedProduct = {
  productName: string;
  currentPrice: number;
  currencyCode?: string;
  productImageUrl?: string;
};

export type EmailAlertProduct = {
  name: string;
  image_url?: string;
  url: string;
  currency: string;
};

export type EmailResult = {
  success: boolean;
  skipped?: boolean;
  error?: unknown;
};

export type PriceCheckDeps = {
  scrapeProduct: (url: string) => Promise<ScrapedProduct>;
  sendPriceDropAlert: (
    userEmail: string,
    product: EmailAlertProduct,
    oldPrice: number,
    newPrice: number,
  ) => Promise<EmailResult>;
};

export type TrackedProduct = {
  id: string;
  url: string;
  current_price: number | string | null;
  currency?: string | null;
  name?: string | null;
  image_url?: string | null;
  user_id: string;
  [key: string]: unknown;
};

export type FailureStage = "scrape" | "parse" | "database";

export type FailedProduct = {
  productId: string;
  url: string;
  stage: FailureStage;
  reason: string;
};

export type UnchangedProduct = {
  productId: string;
  url: string;
  name: string;
  price: number;
  currency: string;
};

export type AlertStatus = "sent" | "failed" | "skipped";

export type AlertOutcome = {
  productId: string;
  url: string;
  userEmail?: string;
  oldPrice: number;
  newPrice: number;
  status: AlertStatus;
  reason?: string;
};

export type ProductUpdateOutcome = {
  productId: string;
  url: string;
  name: string;
  oldPrice: number;
  newPrice: number;
  currency: string;
  priceChanged: boolean;
  priceDirection: "down" | "up";
  historyWritten: boolean;
  alert?: AlertOutcome;
};

export type PriceCheckReport = {
  total: number;
  updated: number;
  unchanged: number;
  failed: number;
  priceChanges: number;
  priceDrops: number;
  alertsSent: number;
  alertsFailed: number;
  historyPruned: number;
  updatedProducts: ProductUpdateOutcome[];
  unchangedProducts: UnchangedProduct[];
  failedProducts: FailedProduct[];
  alerts: AlertOutcome[];
};

type ProductOutcome =
  | { kind: "updated"; outcome: ProductUpdateOutcome }
  | { kind: "unchanged"; outcome: UnchangedProduct }
  | { kind: "failed"; outcome: FailedProduct };

export function normalizeCurrencyCode(value?: string, fallback = "USD") {
  if (!value) return fallback;

  const trimmed = value.trim();
  const upper = trimmed.toUpperCase();

  if (/^[A-Z]{3}$/.test(upper)) return upper;

  const symbolMap: Record<string, string> = {
    "₹": "INR",
    $: "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
  };

  return symbolMap[trimmed] || fallback;
}

export function isAuthorized(request: Request) {
  const authHeader =
    request.headers.get("authorization") ||
    request.headers.get("Authorization");
  const cronSecret = process.env.CRON_SECRET;

  if (!cronSecret) {
    console.error("CRON_SECRET is missing");
    return false;
  }

  const authorized = authHeader === `Bearer ${cronSecret}`;
  if (!authorized) {
    console.warn("Cron auth failed", {
      hasAuthHeader: Boolean(authHeader),
      startsWithBearer: authHeader?.startsWith("Bearer ") ?? false,
    });
  }

  return authorized;
}

export async function pruneOldHistory(
  supabase: SupabaseClient,
  retentionDays: number,
) {
  if (!Number.isFinite(retentionDays) || retentionDays <= 0) return 0;

  const cutoff = new Date(
    Date.now() - retentionDays * 24 * 60 * 60 * 1000,
  ).toISOString();

  const { count, error } = await supabase
    .from("price_history")
    .delete({ count: "exact" })
    .lt("checked_at", cutoff);

  if (error) {
    console.error("Error pruning price history:", {
      message: error.message,
    });
    return 0;
  }

  return count ?? 0;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  try {
    return JSON.stringify(error);
  } catch {
    return String(error);
  }
}

async function processProduct(
  product: TrackedProduct,
  supabase: SupabaseClient,
  deps: PriceCheckDeps,
): Promise<ProductOutcome> {
  // Any failure inside this try/catch becomes a per-product "failed" outcome.
  // Nothing is written before the scrape/parse validation succeeds, so a
  // failed product can never gain price history or trigger an alert email.
  try {
    let productData: ScrapedProduct;
    try {
      productData = await deps.scrapeProduct(product.url);
    } catch (error) {
      return {
        kind: "failed",
        outcome: {
          productId: product.id,
          url: product.url,
          stage: "scrape",
          reason: `Scrape failed: ${errorMessage(error)}`,
        },
      };
    }

    if (productData.currentPrice == null) {
      console.warn("Missing currentPrice from scrape", {
        productId: product.id,
      });
      return {
        kind: "failed",
        outcome: {
          productId: product.id,
          url: product.url,
          stage: "parse",
          reason: "Scraped product data is missing currentPrice",
        },
      };
    }

    const newPrice = Number(productData.currentPrice);
    const oldPrice = Number(product.current_price);

    if (Number.isNaN(newPrice) || Number.isNaN(oldPrice)) {
      console.warn("Invalid product price values", {
        productId: product.id,
        currentPrice: productData.currentPrice,
        previousPrice: product.current_price,
      });
      return {
        kind: "failed",
        outcome: {
          productId: product.id,
          url: product.url,
          stage: "parse",
          reason: `Invalid price values (scraped=${String(
            productData.currentPrice,
          )}, stored=${String(product.current_price)})`,
        },
      };
    }

    const normalizedCurrency = normalizeCurrencyCode(
      productData.currencyCode,
      normalizeCurrencyCode(product.currency ?? undefined, "USD"),
    );

    const updatedName = productData.productName || product.name || "";
    const updatedImage = productData.productImageUrl || product.image_url;

    const { error: productUpdateError } = await supabase
      .from("products")
      .update({
        current_price: newPrice,
        currency: normalizedCurrency,
        name: updatedName,
        image_url: updatedImage,
        updated_at: new Date().toISOString(),
      })
      .eq("id", product.id);

    if (productUpdateError) {
      throw new Error(
        `Product update failed: ${errorMessage(productUpdateError)}`,
      );
    }

    // Price unchanged: refresh the product row but never add history or email.
    if (oldPrice === newPrice) {
      return {
        kind: "unchanged",
        outcome: {
          productId: product.id,
          url: product.url,
          name: updatedName,
          price: newPrice,
          currency: normalizedCurrency,
        },
      };
    }

    const { error: historyError } = await supabase
      .from("price_history")
      .insert({
        product_id: product.id,
        price: newPrice,
        currency: normalizedCurrency,
      });

    if (historyError) {
      // History write failed: stop before any alert so a re-run stays safe.
      throw new Error(
        `Price history insert failed: ${errorMessage(historyError)}`,
      );
    }

    const outcome: ProductUpdateOutcome = {
      productId: product.id,
      url: product.url,
      name: updatedName,
      oldPrice,
      newPrice,
      currency: normalizedCurrency,
      priceChanged: true,
      priceDirection: newPrice < oldPrice ? "down" : "up",
      historyWritten: true,
    };

    if (newPrice < oldPrice) {
      outcome.alert = await sendDropAlert(product, supabase, deps, {
        name: updatedName,
        image_url: updatedImage ?? undefined,
        url: product.url,
        currency: normalizedCurrency,
      }, oldPrice, newPrice);
    }

    return { kind: "updated", outcome };
  } catch (error) {
    console.error(`Error processing product ${product.id}:`, error);
    return {
      kind: "failed",
      outcome: {
        productId: product.id,
        url: product.url,
        stage: "database",
        reason: errorMessage(error),
      },
    };
  }
}

async function sendDropAlert(
  product: TrackedProduct,
  supabase: SupabaseClient,
  deps: PriceCheckDeps,
  emailProduct: EmailAlertProduct,
  oldPrice: number,
  newPrice: number,
): Promise<AlertOutcome> {
  const base: AlertOutcome = {
    productId: product.id,
    url: product.url,
    oldPrice,
    newPrice,
    status: "skipped",
  };

  const { data: userData, error: userError } =
    await supabase.auth.admin.getUserById(product.user_id);

  if (userError) {
    console.error("Unable to fetch user for price-drop alert", {
      productId: product.id,
      userId: product.user_id,
      message: userError.message,
    });
    return {
      ...base,
      status: "skipped",
      reason: `Unable to fetch user: ${userError.message}`,
    };
  }

  const userEmail = userData?.user?.email;
  if (!userEmail) {
    console.warn("User missing email; skipping alert", {
      productId: product.id,
      userId: product.user_id,
    });
    return {
      ...base,
      status: "skipped",
      reason: "User has no email address",
    };
  }

  const emailResult = await deps.sendPriceDropAlert(
    userEmail,
    emailProduct,
    oldPrice,
    newPrice,
  );

  if (emailResult.success) {
    return { ...base, userEmail, status: "sent" };
  }

  console.error("sendPriceDropAlert failed", {
    productId: product.id,
    userId: product.user_id,
    result: emailResult,
  });

  return {
    ...base,
    userEmail,
    status: "failed",
    reason: emailResult.skipped
      ? "Email send was skipped"
      : `Email send failed: ${errorMessage(emailResult.error)}`,
  };
}

export type RunPriceCheckParams = {
  supabase: SupabaseClient;
  deps: PriceCheckDeps;
  retentionDays: number;
};

export async function runPriceCheck({
  supabase,
  deps,
  retentionDays,
}: RunPriceCheckParams): Promise<PriceCheckReport> {
  const { data: products, error: productsError } = await supabase
    .from("products")
    .select("*");

  if (productsError) throw productsError;

  const report: PriceCheckReport = {
    total: products?.length ?? 0,
    updated: 0,
    unchanged: 0,
    failed: 0,
    priceChanges: 0,
    priceDrops: 0,
    alertsSent: 0,
    alertsFailed: 0,
    historyPruned: 0,
    updatedProducts: [],
    unchangedProducts: [],
    failedProducts: [],
    alerts: [],
  };

  if (!products || products.length === 0) {
    console.warn("No products found in DB");
    report.historyPruned = await pruneOldHistory(supabase, retentionDays);
    return report;
  }

  console.log(`Found ${products.length} products to check`);

  // A failure for one product must never abort the rest of the run.
  for (const product of products as TrackedProduct[]) {
    const result = await processProduct(product, supabase, deps);

    if (result.kind === "failed") {
      report.failed++;
      report.failedProducts.push(result.outcome);
      continue;
    }

    if (result.kind === "unchanged") {
      report.unchanged++;
      report.unchangedProducts.push(result.outcome);
      continue;
    }

    report.updated++;
    report.updatedProducts.push(result.outcome);

    if (result.outcome.priceChanged) {
      report.priceChanges++;
      if (result.outcome.priceDirection === "down") {
        report.priceDrops++;
      }
    }

    if (result.outcome.alert) {
      const alert = result.outcome.alert;
      report.alerts.push(alert);
      if (alert.status === "sent") report.alertsSent++;
      if (alert.status === "failed") report.alertsFailed++;
    }
  }

  report.historyPruned = await pruneOldHistory(supabase, retentionDays);

  return report;
}
