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
        self.last_error = None  # last failed search request
        self.detail_error = None  # first failed detail request of the run

    def search(self, was):
        """One search per skill. Returns None when the request itself failed,
        so the caller can tell a failed search apart from an empty result."""
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
                self.last_error = f"Suche '{was}': Status {response.status_code}"
                return None
            jobs = response.json().get("ergebnisliste", [])
            return [p for p in (self._parse_job(j) for j in jobs) if p]
        except Exception as e:
            print(f"Fehler bei Arbeitsamt-Suche '{was}': {e}")
            self.last_error = f"Suche '{was}': {e}"
            return None

    @staticmethod
    def _encode_refnr(refnr):
        return base64.urlsafe_b64encode(refnr.encode()).decode()

    def _fetch_description(self, encoded_refnr):
        # Search results carry no long text, only the search endpoint's
        # short title/occupation field, which starved the scoring badly.
        # One extra request per hit gets the real description. A failure
        # only costs this one description (fallback: short title), so it
        # is recorded on the scraper instead of aborting anything.
        try:
            response = requests.get(
                f"{DETAILS_URL}/{encoded_refnr}", headers=self.headers, timeout=15
            )
            if response.status_code != 200:
                self._record_detail_error(
                    f"Details {encoded_refnr}: Status {response.status_code}"
                )
                return None
            return response.json().get("stellenangebotsBeschreibung")
        except Exception as e:
            self._record_detail_error(f"Details {encoded_refnr}: {e}")
            return None

    def _record_detail_error(self, message):
        if self.detail_error is None:
            self.detail_error = message

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
        from projectMatcher import STATUS_ABORTED, new_outcome, save_matches

        outcome = new_outcome("arbeitsamt")
        self.last_error = None
        self.detail_error = None
        try:
            with Session(engine) as session:
                for skill in profile["skills"]:
                    jobs = self.search(skill)
                    if jobs is None:
                        # A failed search only affects this one skill —
                        # record the stage and keep going with the rest.
                        if outcome["failed_stage"] is None:
                            outcome["failed_stage"] = "suche"
                            outcome["reason"] = self.last_error
                    else:
                        outcome["fetched"] += len(jobs)
                        if jobs:
                            inserted, updated = save_matches(
                                session, matcher, profile, jobs, min_score
                            )
                            outcome["inserted"] += inserted
                            outcome["updated"] += updated
                    time.sleep(random.uniform(1, 2))
            # Detail failures only cost a single description each and
            # never turn the whole run into "abgebrochen".
            if outcome["failed_stage"] is None and self.detail_error:
                outcome["failed_stage"] = "details"
                outcome["reason"] = self.detail_error
        except Exception as e:
            outcome["status"] = STATUS_ABORTED
            outcome["failed_stage"] = outcome["failed_stage"] or "unbekannt"
            outcome["reason"] = outcome["reason"] or str(e)
        return outcome


if __name__ == "__main__":
    from datetime import timezone

    from config import MIN_SCORE, PROFILE
    from database import create_db_and_tables
    from projectMatcher import ProjectMatcher, record_run_outcomes

    create_db_and_tables()
    matcher = ProjectMatcher()
    outcome = ArbeitsamtScraper().scrape(matcher, PROFILE, min_score=MIN_SCORE)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    record_run_outcomes(run_id, [outcome])
    stats = matcher.get_statistics()
    print(f"Matches: {stats['total_matches']}")
    print(f"Durchschnitt Score: {(stats['avg_score'] or 0):.2f}")
