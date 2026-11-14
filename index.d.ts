export interface PriceField {
  discount: number | null;
  currentPrice: number | null;
}

export interface DetailType {
  title: string | null;
  author: string[] | null;
  publisher: string | null;
  publicationDate: string | null;
  imageUrl: string | null;
  introduction: string | null;
  price: PriceField;
  url: string | null;
}

/**
 * Outcome status of one page-level search request.
 *
 * - "items": one or more products were parsed from the page.
 * - "empty": the request succeeded, but the page has no product list
 *   (including an out-of-range page number).
 * - "requestFailed": the request failed (network error, non-2xx response,
 *   or rate-limit retries exhausted); see `reason` for details.
 * - "invalidPage": the given page is not a positive integer; no request
 *   was sent.
 */
export type SearchStatus = "items" | "empty" | "requestFailed" | "invalidPage";

export interface ItemSearchResult {
  status: "items";
  /** The positive integer page that was actually requested. */
  page: number;
  /** Products parsed from the page; the field structure of each item is unchanged. */
  items: DetailType[];
}

export interface EmptyPageSearchResult {
  status: "empty";
  /** The positive integer page that was actually requested. */
  page: number;
  items: [];
}

export interface RequestFailedSearchResult {
  status: "requestFailed";
  /** The positive integer page that was actually requested. */
  page: number;
  /** Human-readable, diagnostics-safe reason (for example an HTTP status). No HTML or stack trace. */
  reason: string;
}

export interface InvalidPageSearchResult {
  status: "invalidPage";
  /** The original, rejected page argument as passed by the caller. */
  page: unknown;
  /** Human-readable reason explaining why the page argument was rejected. */
  reason: string;
}

export type SearchOutcome =
  | ItemSearchResult
  | EmptyPageSearchResult
  | RequestFailedSearchResult
  | InvalidPageSearchResult;

export type BooksComTwCollectionFunction = (
  keyword: string,
  page?: number,
) => Promise<SearchOutcome>;

declare const booksComTwCrawler: BooksComTwCollectionFunction;

export default booksComTwCrawler;
