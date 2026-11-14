"""Synchronous single-page scrape — fetch, extract, return in one call."""
import time
import requests
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import get_pg_connection, verify_api_key
from api.models import PhantomResponse

router = APIRouter(dependencies=[Depends(verify_api_key)])


class ScrapeRequest(BaseModel):
    url: str
    include_markdown: bool = True
    include_entities: bool = True
    include_schemas: bool = True
    timeout: int = Field(default=30, ge=5, le=120)


@router.post("/scrape", response_model=PhantomResponse)
def scrape_url(req: ScrapeRequest):
    """Synchronously scrape a URL: fetch, extract markdown/entities/schemas, return all."""
    parsed = urlparse(req.url)
    if not parsed.scheme or not parsed.netloc:
        raise HTTPException(status_code=422, detail="Invalid URL")
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=422, detail="Only http/https supported")

    # Fetch the page
    start = time.time()
    try:
        resp = requests.get(
            req.url,
            timeout=req.timeout,
            headers={
                "User-Agent": "Phantombot/1.0 (+https://github.com/surya-koritala/phantombot)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="Timeout fetching URL")
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Fetch failed: {str(e)}")

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"Upstream returned HTTP {resp.status_code}",
        )

    html = resp.text
    fetch_time = time.time() - start

    result = {
        "url": req.url,
        "status_code": resp.status_code,
        "fetch_time_seconds": round(fetch_time, 2),
    }

    # Extract markdown
    if req.include_markdown:
        try:
            import sys, pathlib
            intel_dir = str(pathlib.Path(__file__).parent.parent.parent.parent / "intelligence")
            if intel_dir not in sys.path:
                sys.path.insert(0, intel_dir)
            from intelligence.pipelines.markdown import MarkdownPipeline
            md_pipeline = MarkdownPipeline()
            md_results = md_pipeline.extract(html, req.url)
            if md_results:
                r = md_results[0]
                result["markdown"] = r.markdown
                result["markdown_meta"] = {
                    "word_count": r.word_count,
                    "has_code": r.has_code,
                    "has_tables": r.has_tables,
                    "has_images": r.has_images,
                }
        except Exception:
            result["markdown"] = None

    # Extract entities
    if req.include_entities:
        try:
            from intelligence.pipelines.entity import EntityPipeline
            from intelligence.pipelines.entity_resolver import EntityResolver
            entity_pipeline = EntityPipeline()
            resolver = EntityResolver()
            entities = entity_pipeline.extract(html, req.url)
            entities = resolver.resolve(entities)
            result["entities"] = [
                {"text": e.entity_text, "type": e.entity_type, "confidence": e.confidence}
                for e in entities
            ]
            result["entity_count"] = len(entities)
        except Exception:
            result["entities"] = []
            result["entity_count"] = 0

    # Extract schemas
    if req.include_schemas:
        try:
            from intelligence.pipelines.schema import SchemaPipeline
            schema_pipeline = SchemaPipeline()
            schemas = schema_pipeline.extract(html, req.url)
            result["schemas"] = [
                {"type": s.schema_type, "data": s.schema_data, "confidence": s.confidence}
                for s in schemas
            ]
        except Exception:
            result["schemas"] = []

    return PhantomResponse(
        data=result,
        sources=[req.url],
        confidence=0.9,
        freshness=None,
        related=[],
    )
