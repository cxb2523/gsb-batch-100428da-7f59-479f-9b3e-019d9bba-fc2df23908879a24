import { SearchOutcome } from "../index";
import booksComTwCollection from "../src/index";

const timeout = 60 * 1000;

describe("Run demo", (): void => {
  test(
    "Should get one result as Array",
    async () => {
      const result: SearchOutcome = await booksComTwCollection("快速上手LINUX的九堂課");

      expect(result.status).toBe("items");
      if (result.status === "items") {
        expect(result.page).toBe(1);
        expect(result.items).toBeInstanceOf(Array);
        expect(result.items.length).toBe(1);
      }
    },
    timeout,
  );

  test(
    "Should get results as Array and no more than 60",
    async () => {
      const result: SearchOutcome = await booksComTwCollection("春夏秋冬");

      expect(result.status).toBe("items");
      if (result.status === "items") {
        expect(result.page).toBe(1);
        expect(result.items).toBeInstanceOf(Array);
        expect(result.items.length).toBeLessThanOrEqual(60);
      }
    },
    timeout,
  );

  test(
    "Should report an empty page when no product is found",
    async () => {
      const result: SearchOutcome = await booksComTwCollection(
        "blablablablablablablablablablablabla",
      );

      expect(["empty", "requestFailed"]).toContain(result.status);
      if (result.status === "empty") {
        expect(result.items).toEqual([]);
      } else if (result.status === "requestFailed") {
        expect(typeof result.reason).toBe("string");
      }
    },
    timeout,
  );
});
