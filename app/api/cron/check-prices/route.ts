import { sendPriceDropAlert } from "@/lib/email";
import { scrapeProduct } from "@/lib/firecrawl/firecrawl";
import {
  isAuthorized,
  runPriceCheck,
} from "@/lib/cron/price-check";
import { createClient } from "@supabase/supabase-js";
import { NextResponse } from "next/server";

const DEFAULT_HISTORY_RETENTION_DAYS = 90;

function getHistoryRetentionDays() {
  const parsed = Number(process.env.PRICE_HISTORY_RETENTION_DAYS);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return DEFAULT_HISTORY_RETENTION_DAYS;
  }
  return Math.floor(parsed);
}

async function run(request: Request) {
  try {
    if (!isAuthorized(request)) {
      return NextResponse.json({ message: "Unauthorized" }, { status: 401 });
    }

    const supabase = createClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.SUPABASE_SERVICE_ROLE_KEY!,
    );

    const retentionDays = getHistoryRetentionDays();

    const report = await runPriceCheck({
      supabase,
      retentionDays,
      deps: {
        scrapeProduct,
        sendPriceDropAlert,
      },
    });

    const message =
      report.total === 0
        ? "No products to process"
        : "Price check completed";

    return NextResponse.json({
      success: true,
      message,
      results: report,
    });
  } catch (error) {
    console.error("Error checking prices:", error);
    return NextResponse.json(
      { message: "Internal Server Error" },
      { status: 500 },
    );
  }
}

export async function GET(request: Request) {
  return run(request);
}

export async function POST(request: Request) {
  return run(request);
}
