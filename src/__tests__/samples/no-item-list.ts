/**
 * Sample of a normal (HTTP 200) search page whose response contains no
 * product list at all, for example because the page number is out of range.
 * The parser must treat this as an empty page rather than a request failure.
 */
const htmlCode = `
<html>
  <head>
    <title>博客來搜尋結果</title>
  </head>
  <body>
    <div class="search_history">
      <p>沒有符合的商品，請重新輸入查詢條件。</p>
    </div>
  </body>
</html>
`;

export default htmlCode.replace(/^\s+/gm, "").replace(/\n/g, "");
