# Output format

Every row has exactly these fields, in this order:

| Field | Type | Notes |
| --- | --- | --- |
| `name` | string | |
| `address` | string or null | |
| `phone` | string or null | as Google shows it |
| `website` | string or null | Google's `/url?q=` wrapper removed |
| `rating` | number or null | 1.0-5.0 |
| `reviews_count` | integer or null | the number only |
| `category` | string or null | the first of `categories` |
| `categories` | list of strings | may be empty |
| `latitude`, `longitude` | number or null | |
| `place_id` | string or null | Google place id (`ChIJ...`) |
| `google_maps_url` | string or null | `https://www.google.com/maps/search/?api=1&query=<name>&query_place_id=<place_id>` |
| `hours` | object or null | see below |

## Opening hours

```json
{"sunday": "Closed", "monday": "7 AM-6 PM", "friday": "11 AM-2 PM, 5-10 PM", "saturday": "Open 24 hours"}
```

- Keys are English lower-case day names; days Google does not list are missing.
- Values are Google's English text with plain spaces and hyphens; split hours are joined with `", "`.
- `null` when Google lists no hours, when hours were not requested, or when the place request failed (counted in
  `stats["hours_failed"]`).

## CSV

Same columns. `categories` is joined with `"; "`, `hours` is a JSON object, null values are empty cells.
