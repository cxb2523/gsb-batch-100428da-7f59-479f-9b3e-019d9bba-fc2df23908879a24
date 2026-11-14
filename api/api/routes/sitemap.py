"""Site map / URL discovery endpoint."""
import requests as http_requests
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import get_pg_connection, verify_api_key
from api.models import PhantomResponse

router = APIRouter(dependencies=[Depends(verify_api_key)])


class MapRequest(BaseModel):
    url: str  # Domain or URL to map
    include_sitemap: bool = True
    limit: int = Field(default=100, ge=1, le=5000)
    search: str | None = None  # Filter URLs by keyword


@router.post("/map", response_model=PhantomResponse)
def discover_urls(req: MapRequest):
    """Discover URLs for a domain from database + sitemap.xml."""
    parsed = urlparse(req.url)
    domain = parsed.netloc or parsed.path.split("/")[0]
    if not domain:
        raise HTTPException(status_code=422, detail="Could not extract domain from URL")

    urls = set()

    # 1. Check our own database
    pg = get_pg_connection()
    with pg.cursor() as cur:
        if req.search:
            cur.execute(
                "SELECT url FROM crawled_pages WHERE domain = %s AND url ILIKE %s ORDER BY fetched_at DESC LIMIT %s",
                (domain, f"%{req.search}%", req.limit),
            )
        else:
            cur.execute(
                "SELECT url FROM crawled_pages WHERE domain = %s ORDER BY fetched_at DESC LIMIT %s",
                (domain, req.limit),
            )
        for row in cur.fetchall():
            urls.add(row[0])

    db_count = len(urls)

    # 2. Fetch sitemap.xml
    sitemap_urls = []
    if req.include_sitemap:
        sitemap_urls = _fetch_sitemap(domain, req.search, req.limit - len(urls))
        urls.update(sitemap_urls)

    url_list = sorted(urls)[:req.limit]

    return PhantomResponse(
        data={
            "domain": domain,
            "urls": url_list,
            "total": len(url_list),
            "from_database": db_count,
            "from_sitemap": len(sitemap_urls),
        },
        sources=[f"https://{domain}/sitemap.xml"] if sitemap_urls else [],
        confidence=0.9 if url_list else 0.0,
        freshness=None,
        related=[],
    )


def _fetch_sitemap(domain: str, search: str | None, limit: int) -> list[str]:
    """Fetch and parse sitemap.xml for a domain."""
    urls = []
    sitemap_url = f"https://{domain}/sitemap.xml"

    try:
        resp = http_requests.get(
            sitemap_url,
            timeout=10,
            headers={"User-Agent": "Phantombot/1.0"},
        )
        if resp.status_code != 200:
            return []

        root = ET.fromstring(resp.text)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        # Handle sitemap index (contains links to other sitemaps)
        for sitemap in root.findall(".//sm:loc", ns):
            url = sitemap.text
            if url:
                if search and search.lower() not in url.lower():
                    continue
                urls.append(url)
                if len(urls) >= limit:
                    break

        # Also try without namespace (some sites don't use it)
        if not urls:
            for loc in root.iter():
                if loc.tag.endswith("loc") and loc.text:
                    url = loc.text
                    if search and search.lower() not in url.lower():
                        continue
                    urls.append(url)
                    if len(urls) >= limit:
                        break

    except Exception:
        pass  # Sitemap fetch is best-effort

    return urls
