/**
 * Main control for this library.
 */

import {
  BooksComTwCollectionFunction,
  DetailType,
  SearchOutcome,
} from "../index";
import { PageFetchResult, collectionFetch, isPositiveIntegerPage } from "./books-com-tw-fetch";
import { parseSearchPage } from "./item-list-parser";

const booksComTwCollection: BooksComTwCollectionFunction = async (
  keyword: string,
  page: number = 1,
): Promise<SearchOutcome> => {
  // Reject invalid page arguments before any request is sent instead of
  // silently treating them as page 1.
  if (!isPositiveIntegerPage(page)) {
    return {
      status: "invalidPage",
      page,
      reason: "The page argument must be a positive integer.",
    };
  }

  const pageFetchResult: PageFetchResult = await collectionFetch(null, keyword, page);

  if (pageFetchResult.status === "requestFailed") {
    // Network error, non-2xx response, or rate-limit retries exhausted.
    return {
      status: "requestFailed",
      page,
      reason: pageFetchResult.reason,
    };
  }

  // A normal response without a product list (including out-of-range pages)
  // is an empty page, decided by the parser layer from the HTML.
  const parsedPage = parseSearchPage(pageFetchResult.html);

  if (parsedPage.status === "empty") {
    return {
      status: "empty",
      page,
      items: [],
    };
  }

  const items: DetailType[] = parsedPage.items;

  return {
    status: "items",
    page,
    items,
  };
};

export default booksComTwCollection;
