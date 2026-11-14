import axios from "axios";
import { SearchOutcome } from "../index";
import booksComTwCollection from "../src/index";

jest.mock("axios");

describe("Run demo under mocked response", (): void => {
  test("Should report an empty page when the status code from XHR for data not found is 200", async () => {
    type Response = {
      status: number;
      data: string;
    };
    (axios.get as jest.Mock<Promise<Response>>).mockResolvedValueOnce({
      status: 200,
      data: "<html></html>",
    });

    const result: SearchOutcome = await booksComTwCollection("");
    expect(result.status).toBe("empty");
    if (result.status === "empty") {
      expect(result.page).toBe(1);
      expect(result.items).toEqual([]);
    }
  });
});
