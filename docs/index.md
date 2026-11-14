# gmaps-extractor

Search Google Maps for places in an area and get their contact details and opening hours, over plain HTTP (no
browser, no API key).

```sh
pip install gmaps-extractor
gmaps-collect "Austin, Texas" "coffee shop" --max-results 10
```

```python
from gmaps_extractor import collect

for place in collect("Austin, Texas", "coffee shop", max_results=10):
    print(place["name"], place["hours"])
```

How it works:

1. The area name is turned into a bounding box with OpenStreetMap Nominatim (one request).
2. The box is covered with a grid of search cells, searched from the centre outwards.
3. Each cell is searched on Google Maps; places are de-duplicated by `place_id` and places clearly outside the area
   are dropped.
4. For each place, one place request adds its opening hours (skip with `hours=False` / `--no-hours`).

Version 3.0.0 is a smaller library than 2.x; see [Migrating from 2.x](migration.md).

Use it politely: low request rates, modest areas, and respect for Google's Terms of Service and the
[Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/).
