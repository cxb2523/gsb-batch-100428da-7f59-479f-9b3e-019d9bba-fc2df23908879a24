from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    link: Optional[str] = Field(default=None, unique=True)
    company: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[str] = None
    created_date: Optional[str] = None
    is_top_project: bool = False
    is_endcustomer: bool = False
    match_score: float = 0.0
    match_debug: Optional[str] = None
    scrape_date: str = Field(default_factory=_now)
    source: Optional[str] = None  # "freelancermap" | "arbeitsamt"
