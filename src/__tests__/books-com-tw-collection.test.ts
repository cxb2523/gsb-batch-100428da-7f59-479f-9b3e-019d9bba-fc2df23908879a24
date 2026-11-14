import axios from "axios";

import { DetailType, SearchOutcome } from "../../index";
import booksComTwCollection from "../index";
import HtmlCode_CompleteItems from "./samples/complete-items";
import HtmlCode_NoItemList from "./samples/no-item-list";

jest.mock("axios");

const mockedAxiosGet = axios.get as jest.MockedFunction<typeof axios.get>;

describe("booksComTwCollection page-level outcomes", () => {
  beforeEach(() => {
    mockedAxiosGet.mockReset();
  });

  test("returns an 'items' outcome with the parsed items and requested page when products exist", async () => {
    mockedAxiosGet.mockResolvedValueOnce({
      status: 200,
      data: HtmlCode_CompleteItems,
    });

    const result: SearchOutcome = await booksComTwCollection("The Pragmatic Programmer", 3);

    expect(result.status).toBe("items");
    if (result.status !== "items") {
      throw new Error("expected an items outcome");
    }
    expect(result.page).toBe(3);
    expect(result.items).toHaveLength(2);

    // The field structure of each item stays exactly as the parser produces it.
    const firstItem: DetailType = result.items[0];
    expect(firstItem).toEqual({
      author: null,
      imageUrl: "https://www.books.com.tw/img/001/085/63/0010856354.jpg",
      introduction:
        "邁向大師之路 《The PragmaticProgrammer》是少數隨著歲月增長值得一讀再讀的經典書籍，無論您是新手還是實戰豐富的從業者，每次閱讀這本書，您都會得到新的領悟。Dave Thomas和Andy Hunt於......",
      price: {
        currentPrice: 537,
        discount: 79,
      },
      publicationDate: "2020-04-30",
      publisher: "碁峰",
      title: "The Pragmatic Programmer20週年紀念版",
      url: "http://search.books.com.tw/redirect/move/key/The+Pragmatic+Programmer/area/mid/item/0010856354/page/1/idx/8/cat/001/pdf/1/spell/3",
    });

    // The requested page is embedded in the URL.
    expect(mockedAxiosGet).toHaveBeenCalledTimes(1);
    const requestedUrl: string = String(mockedAxiosGet.mock.calls[0][0]);
    expect(requestedUrl).toContain("/page/3/");
  });

  test("returns an 'empty' outcome on a 200 response without a product list", async () => {
    mockedAxiosGet.mockResolvedValueOnce({
      status: 200,
      data: HtmlCode_NoItemList,
    });

    const result: SearchOutcome = await booksComTwCollection("no-such-book", 99);

    expect(result.status).toBe("empty");
    if (result.status !== "empty") {
      throw new Error("expected an empty outcome");
    }
    expect(result.page).toBe(99);
    expect(result.items).toEqual([]);
    expect(mockedAxiosGet).toHaveBeenCalledTimes(1);
  });

  test("returns a 'requestFailed' outcome with a diagnostics-safe reason when the request throws", async () => {
    const error = new Error("Network Error");
    mockedAxiosGet.mockRejectedValueOnce(error);

    const result: SearchOutcome = await booksComTwCollection("anything", 1);

    expect(result.status).toBe("requestFailed");
    if (result.status !== "requestFailed") {
      throw new Error("expected a requestFailed outcome");
    }
    expect(result.page).toBe(1);
    expect(typeof result.reason).toBe("string");
    expect(result.reason.length).toBeGreaterThan(0);
    // The reason must not leak raw HTML or a stack trace.
    expect(result.reason).not.toContain("<");
    expect(result.reason).not.toContain("at ");
    expect(result.reason).toBe("Network Error");
  });

  test("returns a 'requestFailed' outcome carrying the HTTP status for non-2xx responses", async () => {
    mockedAxiosGet.mockRejectedValueOnce({
      response: { status: 500 },
      message: "Request failed with status code 500",
    });

    const result: SearchOutcome = await booksComTwCollection("anything", 2);

    expect(result.status).toBe("requestFailed");
    if (result.status !== "requestFailed") {
      throw new Error("expected a requestFailed outcome");
    }
    expect(result.page).toBe(2);
    expect(result.reason).toBe("HTTP 500");
  });

  test("retries rate-limited (429) responses a bounded number of times and still reports failure", async () => {
    mockedAxiosGet.mockRejectedValue({
      response: { status: 429 },
      message: "Request failed with status code 429",
    });

    const result: SearchOutcome = await booksComTwCollection("limited", 1);

    expect(result.status).toBe("requestFailed");
    if (result.status !== "requestFailed") {
      throw new Error("expected a requestFailed outcome");
    }
    expect(result.page).toBe(1);
    expect(result.reason).toBe("HTTP 429");
    // Initial attempt plus the bounded retries.
    expect(mockedAxiosGet).toHaveBeenCalledTimes(3);
  });

  test.each([0, -1, 1.5, Number.NaN])("rejects invalid page %p before sending any request", async (invalidPage) => {
    const result: SearchOutcome = await booksComTwCollection("keyword", invalidPage);

    expect(result.status).toBe("invalidPage");
    if (result.status !== "invalidPage") {
      throw new Error("expected an invalidPage outcome");
    }
    if (Number.isNaN(invalidPage)) {
      expect(Number.isNaN(result.page as number)).toBe(true);
    } else {
      expect(result.page).toBe(invalidPage);
    }
    expect(typeof result.reason).toBe("string");
    expect(result.reason.length).toBeGreaterThan(0);
    expect(mockedAxiosGet).not.toHaveBeenCalled();
  });

  test("rejects a non-numeric page before sending any request", async () => {
    const result: SearchOutcome = await booksComTwCollection(
      "keyword",
      "1" as unknown as number,
    );

    expect(result.status).toBe("invalidPage");
    if (result.status !== "invalidPage") {
      throw new Error("expected an invalidPage outcome");
    }
    expect(result.page).toBe("1");
    expect(mockedAxiosGet).not.toHaveBeenCalled();
  });

  test("defaults to page 1 when no page argument is provided", async () => {
    mockedAxiosGet.mockResolvedValueOnce({
      status: 200,
      data: HtmlCode_NoItemList,
    });

    const result: SearchOutcome = await booksComTwCollection("keyword");

    expect(result.status).toBe("empty");
    if (result.status !== "empty") {
      throw new Error("expected an empty outcome");
    }
    expect(result.page).toBe(1);
    const requestedUrl: string = String(mockedAxiosGet.mock.calls[0][0]);
    expect(requestedUrl).toContain("/page/1/");
  });
});
