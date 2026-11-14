# Quick start

## Command line

```sh
gmaps-collect "Austin, Texas" "coffee shop" --max-results 10 > coffee.json
gmaps-collect "Lyon, France" "bakery" -o bakeries.csv
```

## Python

```python
from gmaps_extractor import collect

places = collect("Austin, Texas", "coffee shop", max_results=10)
print(len(places), places.stats)
print(places[0])
```

A row looks like this (values made up):

```json
{
  "name": "Example Coffee Roasters",
  "address": "100 Example St, Austin, TX 78701",
  "phone": "(512) 555-0100",
  "website": "https://example-coffee.test/",
  "rating": 4.6,
  "reviews_count": 812,
  "category": "Coffee shop",
  "categories": ["Coffee shop", "Cafe"],
  "latitude": 30.2672,
  "longitude": -97.7431,
  "place_id": "ChIJexample0000000000001",
  "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Example%20Coffee%20Roasters&query_place_id=ChIJexample0000000000001",
  "hours": {"sunday": "Closed", "monday": "7 AM-6 PM", "friday": "11 AM-2 PM, 5-10 PM", "saturday": "Open 24 hours"}
}
```
