import * as cheerio from "cheerio";

/**
 * Description failure strategies:
 * - FAIL: a description page failure aborts the whole scrape (historical behavior, default)
 * - CONTINUE: a description page failure is degraded to description = null while the
 *   remaining product data is still returned
 */
const DESCRIPTION_FAILURE_STRATEGY = {
  FAIL: "fail",
  CONTINUE: "continue",
};

/**
 * Description collection statuses:
 * - SUCCESS: description HTML was fetched successfully
 * - FAILED: the description page navigation/parsing failed
 * - SKIPPED: no description URL was available for the product
 */
const DESCRIPTION_STATUS = {
  SUCCESS: "success",
  FAILED: "failed",
  SKIPPED: "skipped",
};

/**
 * Read the description HTML from the already-navigated description page.
 * Kept as a named export so the parsing step can be unit tested on its own.
 */
const extractDescriptionHtml = async (page) => {
  const descriptionPageHtml = await page.content();
  const $ = cheerio.load(descriptionPageHtml);
  return $("body").html();
};

/**
 * Fetch the product description from its dedicated description page.
 *
 * Returns { descriptionData, descriptionStatus }.
 *
 * With the CONTINUE strategy this function never rejects: failures and the
 * no-URL case are reported through descriptionStatus instead. With the FAIL
 * strategy (default) the original error is rethrown so the whole scrape fails,
 * preserving the historical behavior.
 */
const fetchDescription = async ({
  page,
  descriptionUrl,
  strategy = DESCRIPTION_FAILURE_STRATEGY.FAIL,
  productId,
}) => {
  if (!descriptionUrl) {
    const descriptionStatus = {
      status: DESCRIPTION_STATUS.SKIPPED,
      reason: productId
        ? `No description URL found for product ${productId}; description fetch skipped`
        : "No description URL found; description fetch skipped",
    };
    return { descriptionData: null, descriptionStatus };
  }

  try {
    await page.goto(descriptionUrl);
    const descriptionData = await extractDescriptionHtml(page);
    return {
      descriptionData,
      descriptionStatus: { status: DESCRIPTION_STATUS.SUCCESS, reason: null },
    };
  } catch (error) {
    const reason = `Description page fetch failed for ${descriptionUrl}: ${error.message}`;

    if (strategy === DESCRIPTION_FAILURE_STRATEGY.FAIL) {
      throw error;
    }

    return {
      descriptionData: null,
      descriptionStatus: { status: DESCRIPTION_STATUS.FAILED, reason },
    };
  }
};

export {
  DESCRIPTION_FAILURE_STRATEGY,
  DESCRIPTION_STATUS,
  extractDescriptionHtml,
  fetchDescription,
};
