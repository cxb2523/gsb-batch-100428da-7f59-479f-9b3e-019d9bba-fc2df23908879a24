from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_pg_connection, verify_api_key
from api.models import QueryRequest, PhantomResponse

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.post("/query", response_model=PhantomResponse)
def query_knowledge(req: QueryRequest):
    pg = get_pg_connection()

    if req.include_markdown:
        sql = """
            SELECT e.entity_text, e.entity_type, e.confidence,
                   p.url as source_url, p.fetched_at, e.extracted_at,
                   p.markdown, p.markdown_meta
            FROM entities e
            JOIN crawled_pages p ON e.page_id = p.id
            WHERE e.entity_text ILIKE %s
            ORDER BY e.confidence DESC
            LIMIT %s
        """
    else:
        sql = """
            SELECT e.entity_text, e.entity_type, e.confidence,
                   p.url as source_url, p.fetched_at, e.extracted_at
            FROM entities e
            JOIN crawled_pages p ON e.page_id = p.id
            WHERE e.entity_text ILIKE %s
            ORDER BY e.confidence DESC
            LIMIT %s
        """

    with pg.cursor() as cur:
        cur.execute(sql, (f"%{req.query}%", req.limit))
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    sources = list({r["source_url"] for r in rows})
    avg_confidence = sum(r["confidence"] for r in rows) / len(rows) if rows else 0.0
    freshest = max((r["fetched_at"] for r in rows), default=None)

    # Serialize datetime fields for JSON
    for row in rows:
        for key, val in row.items():
            if isinstance(val, datetime):
                row[key] = val.isoformat()

    return PhantomResponse(
        data=rows,
        sources=sources,
        confidence=round(avg_confidence, 3),
        freshness=freshest,
        related=[],
    )


@router.get("/entity/{entity_id}", response_model=PhantomResponse)
def get_entity(entity_id: str):
    pg = get_pg_connection()
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT e.id, e.entity_text, e.entity_type, e.confidence,
                   p.url as source_url, p.fetched_at
            FROM entities e
            JOIN crawled_pages p ON e.page_id = p.id
            WHERE e.id = %s
            """,
            (entity_id,),
        )
        columns = [desc[0] for desc in cur.description]
        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    data = dict(zip(columns, row))
    for key, val in data.items():
        if isinstance(val, datetime):
            data[key] = val.isoformat()

    return PhantomResponse(
        data=data,
        sources=[data.get("source_url", "")],
        confidence=data.get("confidence", 0.0),
        freshness=data.get("fetched_at"),
        related=[],
    )
