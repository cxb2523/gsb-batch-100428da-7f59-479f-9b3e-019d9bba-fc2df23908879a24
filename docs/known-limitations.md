# Limits and troubleshooting

## Limits

- The library uses Google Maps' internal web endpoints, not the official Places API. Their format is undocumented
  and can change without notice; results then come back empty until the parsers are updated.
- A search is not a complete list of every business: results depend on Google's ranking and on the grid.
- One connection can only search so much. Many requests from one IP address get answered with HTTP 429 or empty
  answers.
- Nominatim allows about one request per second; this library makes one per collection.

## Troubleshooting

| Symptom | Likely cause | What to try |
| --- | --- | --- |
| `RequestError: every search request failed` | Google is blocking this connection, or no network | wait, lower `workers`, raise `min_interval`, use a proxy you are allowed to use |
| `RateLimitError` | too many requests | as above |
| `BoundaryError` | unknown area name | add the region or country: `"Springfield, Illinois, USA"` |
| `ConfigurationError` | `GMAPS_PROXY_USER` set without `GMAPS_PROXY_PASS` (or the reverse), or a URL in `GMAPS_PROXY_HOST` | fix the variables |
| rows have `hours: null` | the place lists no hours, or its request failed | check `stats["hours_failed"]` |
| no rows, no error | nothing matched in that area | try a broader query or a larger area |

Turn on logging to see each failed request:

```python
import logging
logging.basicConfig(level=logging.INFO)
```
