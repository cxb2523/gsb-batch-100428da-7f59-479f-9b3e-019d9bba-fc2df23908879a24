# Migrating from 2.x to 3.0

3.0 is a smaller library: area search plus opening hours. Most 2.x entry points are gone.

| 2.x | 3.0 |
| --- | --- |
| `GMapsExtractor(...).collect(area, category, enrich=True)` | `collect(area, query)` (hours on by default) |
| `collect_v2`, `async_collect_v2`, `stream_collect_v2`, checkpoints and resume | removed; use `collect(..., max_results=...)` |
| `collect_businesses`, `collect_businesses_v2` | `collect` |
| `GMapsClient(settings).search(query, lat, lng)` | `GMapsClient().search_page(query, lat, lng)` |
| `GMapsClient.place_details(...)` | `GMapsClient.place_hours(...)` (opening hours only) |
| reviews: `--reviews`, `GMapsClient.reviews()`, `gmaps-enrich-reviews`, `reviews_data` | removed; reviews moved out of the library |
| `gmaps-server`, `[server]` extra, `gmaps_extractor.decoder` | removed |
| `gmaps-collect-v2` | `gmaps-collect` |
| `config.py`, `GMapsSettings`, `ExtractorConfig` | arguments and `GMAPS_PROXY_*` environment variables |
| `--subdivide` | removed; the grid covers the area |
| `review_count` | `reviews_count` |
| `found_in`, `hex_id`, `ftid`, `is_ad` columns | not in the output (`hex_id` and `ftid` are still returned by `search_page`) |

The command line changed from `gmaps-collect AREA CATEGORY --enrich -o out.json` (which also wrote a CSV next to it)
to `gmaps-collect AREA QUERY -o out.json` (one file, format from the extension; hours on unless `--no-hours`).
