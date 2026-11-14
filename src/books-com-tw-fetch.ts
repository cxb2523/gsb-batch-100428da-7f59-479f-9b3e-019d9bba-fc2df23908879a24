/**
 * To fetch data via books.com.tw.
 *
 * This layer owns URL construction and network-level failures. It never
 * inspects the HTML for products (that is the parser layer's job) and never
 * exposes raw HTML or error stack traces to callers.
 */

import axios, { AxiosResponse } from "axios";

export type PageFetchStatus = "success" | "requestFailed";

export interface PageFetchSuccess {
  status: "success";
  url: string;
  html: string;
}

export interface PageFetchFailure {
  status: "requestFailed";
  url: string;
  /** Diagnostics-safe reason such as an HTTP status. Never contains HTML or a stack trace. */
  reason: string;
}

export type PageFetchResult = PageFetchSuccess | PageFetchFailure;

export interface FetchOptions {
  /** How many retries are allowed after the initial attempt when rate limited (HTTP 429). */
  maxRetries?: number;
  /** Delay in milliseconds before the first retry; doubled for each following retry. */
  retryDelayMs?: number;
}

const DEFAULT_MAX_RETRIES = 2;
const DEFAULT_RETRY_DELAY_MS = 100;
const RATE_LIMIT_STATUS = 429;

const removeLeftoverCode = (htmlCode: string): string => {
  let result: string = htmlCode.replace("\\t", "");
  result = result.replace("\\n", "");
  result = result.replace(/\s+/gi, " ");

  return result;
};

const setKeywordToInsertUrl = (keyword: string): string => {
  // To remove special characters
  let temp: string = keyword.replace(/[~!@#$%^&*()_+\-=}{[\]|"':;?/.,<>}\\]/gi, " ");
  // To remove two or more consequent spaces
  temp = temp.replace(/\s+/, " ");
  // To remove last space
  temp = temp.replace(/\s+$/, "");

  return encodeURI(temp);
};

const setPageToInsertUrl = (page: number): number => page;

const setUrl = (keyword: string, page: number): string => {
  const tempKeyword: string = setKeywordToInsertUrl(keyword);
  const tempPage: number = setPageToInsertUrl(page);

  return `https://search.books.com.tw/search/query/cat/all/sort/1/v/0/ms2/ms2_1/page/${tempPage}/key/${tempKeyword}`;
};

/**
 * To decide whether a page argument is a positive integer before any request
 * is sent. Non-numbers, NaN, decimals, zero and negative numbers are rejected.
 */
export const isPositiveIntegerPage = (page: unknown): page is number =>
  typeof page === "number" && Number.isInteger(page) && page > 0;

const sleep = (milliseconds: number): Promise<void> =>
  new Promise((resolve: () => void): void => {
    setTimeout(resolve, milliseconds);
  });

interface ErrorWithStatus {
  response?: {
    status?: number;
  };
  message?: string;
}

const isErrorWithStatus = (error: unknown): error is ErrorWithStatus =>
  typeof error === "object" && error !== null;

const getFailureReason = (error: unknown): string => {
  if (isErrorWithStatus(error) && error.response && typeof error.response.status === "number") {
    return `HTTP ${error.response.status}`;
  }
  if (isErrorWithStatus(error) && typeof error.message === "string" && error.message !== "") {
    return error.message;
  }

  return "Unknown network error";
};

const isRateLimitedError = (error: unknown): boolean =>
  isErrorWithStatus(error) && error.response?.status === RATE_LIMIT_STATUS;

const fetchFullHtmlCode = async (
  url: string,
  maxRetries: number,
  retryDelayMs: number,
): Promise<string> => {
  let attempt = 0;
  for (;;) {
    try {
      const response: AxiosResponse = await axios.get(url);

      return removeLeftoverCode(response.data);
    } catch (error) {
      // Only rate-limit responses are worth a bounded retry; everything else fails at once.
      if (!isRateLimitedError(error) || attempt >= maxRetries) {
        throw error;
      }
      await sleep(retryDelayMs * 2 ** attempt);
      attempt += 1;
    }
  }
};

const setUrlFollowParameter = (url: string | null, keyword: string, page: number): string => {
  if (url) {
    return url;
  }
  const combineUrl: string = setUrl(keyword, page);

  return combineUrl;
};

export const collectionFetch = async (
  url: string | null,
  keyword: string = "",
  page: number,
  options: FetchOptions = {},
): Promise<PageFetchResult> => {
  const maxRetries: number =
    options.maxRetries === undefined ? DEFAULT_MAX_RETRIES : options.maxRetries;
  const retryDelayMs: number =
    options.retryDelayMs === undefined ? DEFAULT_RETRY_DELAY_MS : options.retryDelayMs;
  const fullUrl: string = setUrlFollowParameter(url, keyword, page);

  try {
    const html: string = await fetchFullHtmlCode(fullUrl, maxRetries, retryDelayMs);

    return { status: "success", url: fullUrl, html };
  } catch (error) {
    // Keep a diagnostics-safe reason; raw HTML and stack traces must not leak out.
    return {
      status: "requestFailed",
      url: fullUrl,
      reason: getFailureReason(error),
    };
  }
};
