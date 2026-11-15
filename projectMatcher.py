import csv
import math
import os
import random
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from sqlmodel import Session, col, select

from config import MAX_PAGES, MIN_SCORE, PROFILE, SOURCES
from database import create_db_and_tables, engine
from models import Project

load_dotenv()

FREELANCERMAP_USERNAME = os.getenv("FREELANCERMAP_USERNAME")
FREELANCERMAP_PASSWORD = os.getenv("FREELANCERMAP_PASSWORD")


class FreelancermapScraper:
    def __init__(self, username, password, max_pages=2):
        self.base_url = "https://www.freelancermap.de"
        self.login_url = f"{self.base_url}/login"
        self.username = username
        self.password = password
        self.max_pages = max_pages
        self.session = requests.Session()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:135.0) Gecko/20100101 Firefox/135.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de,en-US;q=0.7,en;q=0.3",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "DNT": "1",
            "Sec-GPC": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
        }

    def login(self):
        try:
            print("Starte Login-Prozess...")
            self.session.get(self.login_url, headers=self.headers)

            login_headers = {
                **self.headers,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://www.freelancermap.de/login",
                "Origin": "https://www.freelancermap.de",
            }
            login_response = self.session.post(
                self.login_url,
                data={
                    "login": self.username,
                    "password": self.password,
                    "_remember_me": "1",
                },
                headers=login_headers,
                allow_redirects=True,
            )
            print(f"Login-Status: {login_response.status_code}")

            if login_response.status_code in [200, 302]:
                time.sleep(2)
                account_page = self.session.get(
                    "https://www.freelancermap.de/mein_account.html",
                    headers={
                        **self.headers,
                        "Referer": "https://www.freelancermap.de/login",
                    },
                )
                if account_page.status_code == 200 and any(
                    ind in account_page.text
                    for ind in ["Mein Konto", "Profil", "Logout", "Abmelden"]
                ):
                    print("Login erfolgreich!")
                    return True
            return False
        except Exception as e:
            print(f"Fehler beim Login: {e}")
            return False

    def get_page_url(self, page_number):
        params = [
            "contractTypes[]=contracting",
            "remoteInPercent[]=100",
            "countries[]=1",
            "countries[]=2",
            "countries[]=3",
            "sort=1",
            f"pagenr={page_number}",
        ]
        return f"{self.base_url}/project/search/ajax?{'&'.join(params)}"

    def get_page(self, page_number):
        url = self.get_page_url(page_number)
        print(f"\nAbrufen von Seite {page_number}: {url}")
        try:
            ajax_headers = {
                **self.headers,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            }
            response = self.session.get(url, headers=ajax_headers)
            if response.status_code != 200:
                print(f"Fehler: Status Code {response.status_code}")
                return []
            data = response.json()
            if isinstance(data, dict) and data.get("redirect"):
                print("Session abgelaufen, versuche erneuten Login...")
                if self.login():
                    return self.get_page(page_number)
                return []
            projects = (
                data
                if isinstance(data, list)
                else data.get("projects", data.get("hits", []))
            )
            if not projects:
                return []
            return [
                p for p in (self._parse_project_json(proj) for proj in projects) if p
            ]
        except Exception as e:
            import traceback

            print(f"Fehler beim Laden von Seite {page_number}: {e}")
            print(traceback.format_exc())
            return []

    def _parse_project_json(self, project):
        try:
            project_path = project.get("links", {}).get("project", "")
            link = f"{self.base_url}{project_path}" if project_path else "N/A"

            desc_html = project.get("description", "")
            description = (
                BeautifulSoup(desc_html, "html.parser").get_text(
                    separator=" ", strip=True
                )
                if desc_html
                else "N/A"
            )

            created_raw = project.get("created", "")
            try:
                created_date = datetime.fromisoformat(created_raw).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            except (ValueError, TypeError):
                created_date = created_raw or "N/A"

            skill_names = []
            for sk in project.get("skills") or []:
                if isinstance(sk, dict):
                    skill_names += [sk.get("de"), sk.get("en")]
            keywords = ", ".join(dict.fromkeys(n for n in skill_names if n)) or "N/A"

            return {
                "title": project.get("title", "N/A"),
                "link": link,
                "company": project.get("company", "N/A"),
                "description": description,
                "keywords": keywords,
                "created_date": created_date,
                "is_top_project": project.get("topProject") is not None,
                "is_endcustomer": bool(project.get("endcustomer", False)),
                "source": "freelancermap",
            }
        except Exception as e:
            print(f"Fehler beim Parsen: {e}")
            return None

    def scrape(self, matcher, profile, min_score=30):
        if not self.login():
            return
        with Session(engine) as session:
            for page in range(1, self.max_pages + 1):
                projects = self.get_page(page)
                if not projects:
                    break
                save_matches(session, matcher, profile, projects, min_score)
                if page < self.max_pages:
                    time.sleep(random.uniform(2, 4))


def save_matches(session, matcher, profile, candidates, min_score):
    """Score, dedupe (by link) and persist a batch of raw project dicts.

    Shared by every scraper source so the DB-write logic lives in one place.
    """
    for p in candidates:
        candidate = Project(**p)
        score, debug = matcher.calculate_match_score(candidate, profile)
        if score < min_score:
            continue
        candidate.match_score = score
        candidate.match_debug = debug

        existing = session.exec(
            select(Project).where(Project.link == candidate.link)
        ).first()
        if existing:
            existing.title = candidate.title
            existing.company = candidate.company
            existing.description = candidate.description
            existing.keywords = candidate.keywords
            existing.created_date = candidate.created_date
            existing.is_top_project = candidate.is_top_project
            existing.is_endcustomer = candidate.is_endcustomer
            existing.match_score = score
            existing.match_debug = debug
            existing.scrape_date = candidate.scrape_date
            existing.source = candidate.source
        else:
            session.add(candidate)
    session.commit()


class ProjectMatcher:
    def calculate_match_score(self, project: Project, profile):
        score = 0
        debug_info = []

        excluded = []
        for field in (project.description, project.keywords, project.title):
            if field:
                excluded.extend(
                    k
                    for k in profile["excluded_keywords"]
                    if k.lower() in field.lower()
                )
        if excluded:
            debug_info.append(f"Ausgeschlossen wegen: {list(set(excluded))}")
            return 0, "\n".join(debug_info)

        if project.keywords and project.keywords != "N/A":
            project_keywords = set(
                kw.strip().lower() for kw in project.keywords.split(",")
            )
            profile_skills = set(s.lower() for s in profile["skills"])

            exact_matches = project_keywords & profile_skills
            exact_score = (len(exact_matches) / max(len(project_keywords), 1)) * 30

            partial_matches = {
                pk
                for pk in project_keywords
                for s in profile_skills
                if pk != s and (s in pk or pk in s)
            }
            partial_score = (len(partial_matches) / max(len(project_keywords), 1)) * 20

            score += exact_score + partial_score
            debug_info += [
                f"Exact Keyword Score: {exact_score:.2f}",
                f"Partial Keyword Score: {partial_score:.2f}",
                f"Exact Matches: {exact_matches}",
                f"Partial Matches: {partial_matches}",
            ]

        if project.description:
            desc_lower = project.description.lower()
            matching_skills = [
                s.lower() for s in profile["skills"] if s.lower() in desc_lower
            ]
            skill_score = min(len(matching_skills) * 4, 20)
            matching_preferred = [
                k.lower()
                for k in profile["preferred_keywords"]
                if k.lower() in desc_lower
            ]
            preferred_score = min(len(matching_preferred) * 2, 10)
            score += skill_score + preferred_score
            debug_info += [
                f"Description Skills Score: {skill_score}",
                f"Description Preferred Score: {preferred_score}",
                f"Skills in Description: {matching_skills}",
                f"Preferred in Description: {matching_preferred}",
            ]

        if project.created_date is not None:
            try:
                created_date_str: str = project.created_date
                created = datetime.strptime(created_date_str, "%Y-%m-%d %H:%M:%S")
                days_old = (datetime.now() - created).days
                time_score = 20 * math.exp(-days_old / 15)
            except (ValueError, TypeError):
                time_score = 0
                days_old = -1
        else:
            time_score = 0
            days_old = -1
        score += time_score
        debug_info += [f"Time Score: {time_score:.2f}", f"Days Old: {days_old}"]

        return score, "\n".join(debug_info)

    def get_statistics(self):
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with Session(engine) as session:
            from sqlalchemy import func
            from sqlalchemy import select as sa_select

            row = session.execute(
                sa_select(
                    func.avg(col(Project.match_score)).label("avg_score"),
                    func.count(col(Project.id)).label("total_matches"),
                    func.max(col(Project.scrape_date)).label("latest_match"),
                    func.min(col(Project.created_date)).label("oldest_project"),
                    func.count(func.distinct(col(Project.company))).label(
                        "unique_companies"
                    ),
                ).where(col(Project.scrape_date) >= seven_days_ago)
            ).one()
            return dict(row._mapping)

    def export_matches(self, min_score=30, export_path=None):
        if export_path is None:
            export_path = f"matches_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with Session(engine) as session:
            from sqlalchemy import select as sa_select

            rows = session.execute(
                sa_select(
                    col(Project.title),
                    col(Project.company),
                    col(Project.keywords),
                    col(Project.description),
                    col(Project.created_date),
                    col(Project.link),
                    col(Project.is_top_project),
                    col(Project.is_endcustomer),
                    col(Project.match_score),
                    col(Project.match_debug),
                )
                .where(col(Project.match_score) >= min_score)
                .order_by(col(Project.match_score).desc())
            ).all()

        headers = [
            "title",
            "company",
            "keywords",
            "description",
            "created_date",
            "link",
            "is_top_project",
            "is_endcustomer",
            "match_score",
            "match_debug",
        ]
        with open(export_path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(headers)
            w.writerows(rows)
        return export_path


if __name__ == "__main__":
    create_db_and_tables()

    matcher = ProjectMatcher()

    if "freelancermap" in SOURCES:
        try:
            scraper = FreelancermapScraper(
                username=FREELANCERMAP_USERNAME,
                password=FREELANCERMAP_PASSWORD,
                max_pages=MAX_PAGES,
            )
            scraper.scrape(matcher, PROFILE, min_score=MIN_SCORE)
        except Exception as e:
            print(f"freelancermap-Quelle abgebrochen: {e}")

    if "arbeitsamt" in SOURCES:
        try:
            from arbeitsamt import ArbeitsamtScraper

            ArbeitsamtScraper().scrape(matcher, PROFILE, min_score=MIN_SCORE)
        except Exception as e:
            print(f"arbeitsamt-Quelle abgebrochen: {e}")

    stats = matcher.get_statistics()

    print(f"Matches: {stats['total_matches']}")
    print(f"Durchschnitt Score: {(stats['avg_score'] or 0):.2f}")

    matcher.export_matches(min_score=MIN_SCORE)
