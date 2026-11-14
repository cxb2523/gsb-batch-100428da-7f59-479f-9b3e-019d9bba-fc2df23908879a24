import sys
import pathlib
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_pg_connection, verify_api_key
from api.models import PhantomResponse

router = APIRouter(dependencies=[Depends(verify_api_key)])


def _extract_title_and_content_type(html: str | None, url: str) -> dict:
    """Extract title and content_type from HTML using simple parsing."""
    if not html:
        return {"title": None, "content_type": None}
    try:
        # Simple title extraction without heavy dependencies
        import re
        title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else None
        return {"title": title, "content_type": None}
    except Exception:
        return {"title": None, "content_type": None}


@router.get("/page/{page_id}/markdown", response_model=PhantomResponse)
def get_page_markdown(page_id: str, chunking: str = "none", chunk_size: int = 512):
    """Return clean markdown for a crawled page, with optional chunking."""
    pg = get_pg_connection()
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT id, url, markdown, markdown_meta, fetched_at, html
            FROM crawled_pages
            WHERE id = %s
            """,
            (page_id,),
        )
        columns = [desc[0] for desc in cur.description]
        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Page not found")

    data = dict(zip(columns, row))

    if data.get("markdown") is None:
        raise HTTPException(status_code=404, detail="Markdown not available for this page")

    # Serialize datetime fields
    for key, val in data.items():
        if isinstance(val, datetime):
            data[key] = val.isoformat()

    # Convert UUID to string
    data["id"] = str(data["id"])

    # Extract rich metadata from HTML
    html_content = data.get("html")
    rich_meta = _extract_title_and_content_type(html_content, data["url"])

    markdown_text = data["markdown"]

    # Build response data
    response_data: dict = {
        "url": data["url"],
        "markdown": markdown_text,
        "meta": data.get("markdown_meta"),
        "title": rich_meta["title"],
        "content_type": rich_meta["content_type"],
    }

    # Apply chunking if requested
    if chunking == "recursive":
        try:
            intel_dir = str(pathlib.Path(__file__).parent.parent.parent.parent / "intelligence")
            if intel_dir not in sys.path:
                sys.path.insert(0, intel_dir)
            from intelligence.chunker import chunk_recursive
            chunks = chunk_recursive(markdown_text, max_tokens=chunk_size)
            response_data["chunks"] = chunks
            response_data["chunk_strategy"] = "recursive"
            # When chunking, omit full markdown to keep payload manageable
            response_data["markdown"] = None
        except Exception:
            pass
    elif chunking == "headings":
        try:
            intel_dir = str(pathlib.Path(__file__).parent.parent.parent.parent / "intelligence")
            if intel_dir not in sys.path:
                sys.path.insert(0, intel_dir)
            from intelligence.chunker import chunk_by_headings
            chunks = chunk_by_headings(markdown_text)
            response_data["chunks"] = chunks
            response_data["chunk_strategy"] = "headings"
            response_data["markdown"] = None
        except Exception:
            pass

    return PhantomResponse(
        data=response_data,
        sources=[data["url"]],
        confidence=data.get("markdown_meta", {}).get("confidence", 0.0) if data.get("markdown_meta") else 0.0,
        freshness=data.get("fetched_at"),
        related=[],
    )
