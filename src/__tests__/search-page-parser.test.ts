import HtmlCode_CompleteItems from "./samples/complete-items";
import HtmlCode_EmptyItem from "./samples/empty-item";
import HtmlCode_NoItemList from "./samples/no-item-list";

import { parseSearchPage } from "../item-list-parser";

describe("parseSearchPage", () => {
  test("judges an HTML page with products as 'items' and keeps the item fields", () => {
    const output = parseSearchPage(HtmlCode_CompleteItems);

    expect(output.status).toBe("items");
    expect(output.items).toHaveLength(2);
    expect(Object.keys(output.items[0])).toEqual([
      "title",
      "url",
      "author",
      "publisher",
      "publicationDate",
      "imageUrl",
      "price",
      "introduction",
    ]);
  });

  test("judges a 200 page containing the table but no product rows as 'empty'", () => {
    const output = parseSearchPage(HtmlCode_EmptyItem);

    expect(output.status).toBe("empty");
    expect(output.items).toEqual([]);
  });

  test("judges a 200 page without any product list as 'empty'", () => {
    const output = parseSearchPage(HtmlCode_NoItemList);

    expect(output.status).toBe("empty");
    expect(output.items).toEqual([]);
  });

  test("judges unrelated HTML as 'empty'", () => {
    const output = parseSearchPage("<body></body>");

    expect(output.status).toBe("empty");
    expect(output.items).toEqual([]);
  });
});
