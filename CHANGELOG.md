# 2026-11-14

*Features*

- Product detail parsing is now fault-tolerant per GraphQL block: a missing or malformed block only nulls the fields it provides instead of failing the whole product
- Skipped GraphQL blocks are logged with their block name and product URL to make page-layout changes easier to locate
- Added local tests (`npm test`) covering complete, partially missing and entirely missing GraphQL caches, plus dataset schema conformance and quota behavior

*Fixes*

- A product whose name and SKU cannot both be read is treated as invalid: it is no longer saved and no longer consumes the `maxItems` quota, with an explicit log message explaining why
- A product that can be partially parsed now always writes exactly one dataset record and decrements the remaining quota exactly once

# 2026-10-15

*Features*

- Added an output schema and descriptions of the dataset fields

*Fixes*

- Read products from Zalando's new page data, so runs return items again instead of none
- Removed `comingSoon` from the output (Zalando no longer provides it)
- A run that finds no products now ends as failed, with a message
- Upgraded to Node 22, Apify SDK 3.7 and Crawlee 3.18

# 2023-10-26

*Fixes*

- Removed user ratings and reviews from the output (Zalando no longer provides them)
