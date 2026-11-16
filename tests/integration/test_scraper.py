from datetime import datetime, timedelta

from scraper import Scraper
from storage import StationsStorage

today = datetime.now()

origin = StationsStorage.get_station("MADRID (TODAS)")
destination = StationsStorage.get_station("BARCELONA (TODAS)")
departure_date = today + timedelta(days=2)
return_date = today + timedelta(days=3)

scraper = Scraper(origin, destination, departure_date, return_date)

result = scraper.get_trainrides()

assert result.success, f"Scraping did not succeed: status={result.status}, stage={result.stage}, error={result.error!r}"
assert len(result.rides) > 0, "There are no trains from Madrid to Barcelona (which is impossible)"
