import base64
import random
import time
from datetime import datetime

import requests
from sqlmodel import Session

from database import engine

SEARCH_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs"
DETAILS_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobdetails"
API_KEY = "jobboerse-jobsuche"  # public, reverse-engineered client id, no signup needed
ANGEBOTSART_SELBSTAENDIGKEIT = 2


class ArbeitsamtScraper:
    """Scrapes self-employment ('Selbstständigkeit') listings from the
    Bundesagentur für Arbeit Jobsuche API, one search per profile skill.
    """

    def __init__(self):
        self.headers = {"X-API-Key": API_KEY}

    def search(self, was):
        try:
            response = requests.get(
                SEARCH_URL,
                params={
                    "was": was,
                    "angebotsart": ANGEBOTSART_SELBSTAENDIGKEIT,
                    "size": 25,
                },
                headers=self.headers,
                timeout=15,
            )
            if response.status_code != 200:
                print(f"Arbeitsamt-API Fehler ({was}): Status {response.status_code}")
                return []
            jobs = response.json().get("ergebnisliste", [])
            return [p for p in (self._parse_job(j) for j in jobs) if p]
        except Exception as e:
            print(f"Fehler bei Arbeitsamt-Suche '{was}': {e}")
            return []

    @staticmethod
    def _encode_refnr(refnr):
        return base64.urlsafe_b64encode(refnr.encode()).decode()

    def _fetch_description(self, encoded_refnr):
        # Search results carry no long text, only the search endpoint's
        # short title/occupation field, which starved the scoring badly.
        # One extra request per hit gets the real description.
        try:
            response = requests.get(
                f"{DETAILS_URL}/{encoded_refnr}", headers=self.headers, timeout=15
            )
            if response.status_code != 200:
                return None
            return response.json().get("stellenangebotsBeschreibung")
        except Exception:
            return None

    def _parse_job(self, job):
        try:
            refnr = job.get("referenznummer", "")
            encoded_refnr = self._encode_refnr(refnr) if refnr else ""
            link = (
                f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{encoded_refnr}"
                if encoded_refnr
                else "N/A"
            )

            created_raw = job.get("datumErsteVeroeffentlichung", "")
            try:
                created_date = datetime.fromisoformat(created_raw).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            except (ValueError, TypeError):
                created_date = created_raw or "N/A"

            title = job.get("stellenangebotsTitel") or job.get("hauptberuf", "N/A")
            description = (
                self._fetch_description(encoded_refnr) if encoded_refnr else None
            ) or job.get("hauptberuf", title)

            return {
                "title": title,
                "link": link,
                "company": job.get("firma", "N/A"),
                "description": description,
                "keywords": "N/A",
                "created_date": created_date,
                "is_top_project": False,
                "is_endcustomer": False,
                "source": "arbeitsamt",
            }
        except Exception as e:
            print(f"Fehler beim Parsen (Arbeitsamt): {e}")
            return None

    def scrape(self, matcher, profile, min_score=30):
        from projectMatcher import save_matches

        with Session(engine) as session:
            for skill in profile["skills"]:
                jobs = self.search(skill)
                if jobs:
                    save_matches(session, matcher, profile, jobs, min_score)
                time.sleep(random.uniform(1, 2))


if __name__ == "__main__":
    from config import MIN_SCORE, PROFILE
    from database import create_db_and_tables
    from projectMatcher import ProjectMatcher

    create_db_and_tables()
    matcher = ProjectMatcher()
    ArbeitsamtScraper().scrape(matcher, PROFILE, min_score=MIN_SCORE)
    stats = matcher.get_statistics()
    print(f"Matches: {stats['total_matches']}")
    print(f"Durchschnitt Score: {(stats['avg_score'] or 0):.2f}")
