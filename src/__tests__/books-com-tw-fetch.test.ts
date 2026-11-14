import axios from "axios";

import { collectionFetch, isPositiveIntegerPage } from "../books-com-tw-fetch";

jest.mock("axios");

const mockedAxiosGet = axios.get as jest.MockedFunction<typeof axios.get>;

describe("collectionFetch", () => {
  beforeEach(() => {
    mockedAxiosGet.mockReset();
  });

  test("builds the search URL with the encoded keyword and requested page", async () => {
    mockedAxiosGet.mockResolvedValueOnce({ status: 200, data: "<html>data</html>" });

    const result = await collectionFetch(null, "Clean Coder", 7);

    expect(result.status).toBe("success");
    if (result.status !== "success") {
      throw new Error("expected a success outcome");
    }
    expect(result.url).toBe(
      "https://search.books.com.tw/search/query/cat/all/sort/1/v/0/ms2/ms2_1/page/7/key/Clean%20Coder",
    );
    expect(mockedAxiosGet).toHaveBeenCalledTimes(1);
    expect(mockedAxiosGet.mock.calls[0][0]).toBe(result.url);
  });

  test("uses a caller-provided URL as-is", async () => {
    mockedAxiosGet.mockResolvedValueOnce({ status: 200, data: "<html></html>" });

    const result = await collectionFetch("https://example.test/custom", "keyword", 1);

    expect(result.status).toBe("success");
    if (result.status !== "success") {
      throw new Error("expected a success outcome");
    }
    expect(result.url).toBe("https://example.test/custom");
  });

  test("returns a failure with the HTTP status reason and no HTML when the response errors", async () => {
    mockedAxiosGet.mockRejectedValueOnce({
      response: { status: 404, data: "<html>should not leak</html>" },
      message: "Request failed with status code 404",
    });

    const result = await collectionFetch(null, "keyword", 1);

    expect(result.status).toBe("requestFailed");
    if (result.status !== "requestFailed") {
      throw new Error("expected a requestFailed outcome");
    }
    expect(result.reason).toBe("HTTP 404");
    expect(result.reason).not.toContain("<");
    expect(result).not.toHaveProperty("html");
  });

  test("falls back to the error message when there is no HTTP response", async () => {
    mockedAxiosGet.mockRejectedValueOnce(new Error("socket hang up"));

    const result = await collectionFetch(null, "keyword", 1);

    expect(result.status).toBe("requestFailed");
    if (result.status !== "requestFailed") {
      throw new Error("expected a requestFailed outcome");
    }
    expect(result.reason).toBe("socket hang up");
  });

  test("uses a generic reason when the rejection carries no usable information", async () => {
    mockedAxiosGet.mockRejectedValueOnce("something went wrong");

    const result = await collectionFetch(null, "keyword", 1);

    expect(result.status).toBe("requestFailed");
    if (result.status !== "requestFailed") {
      throw new Error("expected a requestFailed outcome");
    }
    expect(result.reason).toBe("Unknown network error");
  });

  test("retries 429 responses up to the configured limit before failing", async () => {
    mockedAxiosGet.mockRejectedValue({
      response: { status: 429 },
      message: "Request failed with status code 429",
    });

    const result = await collectionFetch(null, "keyword", 1, {
      maxRetries: 1,
      retryDelayMs: 1,
    });

    expect(result.status).toBe("requestFailed");
    if (result.status !== "requestFailed") {
      throw new Error("expected a requestFailed outcome");
    }
    expect(result.reason).toBe("HTTP 429");
    expect(mockedAxiosGet).toHaveBeenCalledTimes(2);
  });

  test("succeeds when a retried 429 response is followed by a normal response", async () => {
    mockedAxiosGet
      .mockRejectedValueOnce({ response: { status: 429 } })
      .mockResolvedValueOnce({ status: 200, data: "<html>ok</html>" });

    const result = await collectionFetch(null, "keyword", 1, {
      maxRetries: 2,
      retryDelayMs: 1,
    });

    expect(result.status).toBe("success");
    expect(mockedAxiosGet).toHaveBeenCalledTimes(2);
  });

  test("does not retry non-429 errors", async () => {
    mockedAxiosGet.mockRejectedValueOnce({
      response: { status: 503 },
      message: "Request failed with status code 503",
    });

    const result = await collectionFetch(null, "keyword", 1, {
      maxRetries: 3,
      retryDelayMs: 1,
    });

    expect(result.status).toBe("requestFailed");
    if (result.status !== "requestFailed") {
      throw new Error("expected a requestFailed outcome");
    }
    expect(result.reason).toBe("HTTP 503");
    expect(mockedAxiosGet).toHaveBeenCalledTimes(1);
  });
});

describe("isPositiveIntegerPage", () => {
  test.each([
    [1, true],
    [2, true],
    [999, true],
    [0, false],
    [-1, false],
    [1.2, false],
    [Number.NaN, false],
    [Number.POSITIVE_INFINITY, false],
  ])("validates %p as %s", (page, expected) => {
    expect(isPositiveIntegerPage(page)).toBe(expected);
  });

  test("rejects non-number values", () => {
    expect(isPositiveIntegerPage("1")).toBe(false);
    expect(isPositiveIntegerPage(null)).toBe(false);
    expect(isPositiveIntegerPage(undefined)).toBe(false);
    expect(isPositiveIntegerPage(true)).toBe(false);
  });
});
