from datetime import datetime

from fastapi import APIRouter, Depends

from api.dependencies import get_qdrant_client, get_embedder, get_pg_connection, verify_api_key
from api.config import Config
from api.models import SearchRequest, PhantomResponse

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.post("/search", response_model=PhantomResponse)
def semantic_search(req: SearchRequest):
    embedder = get_embedder()
    qdrant = get_qdrant_client()

    embedding = embedder.encode(req.query).tolist()
    results = qdrant.query_points(
        collection_name=Config.QDRANT_COLLECTION,
        query=embedding,
        limit=req.limit,
    )

    data = []
    for point in results.points:
        item = {
            "id": point.id,
            "score": point.score,
            **point.payload,
        }
        data.append(item)

    # If include_markdown requested, fetch markdown for each page_id
    if req.include_markdown and data:
        page_ids = list({d.get("page_id") for d in data if d.get("page_id")})
        if page_ids:
            pg = get_pg_connection()
            with pg.cursor() as cur:
                placeholders = ",".join(["%s"] * len(page_ids))
                cur.execute(
                    f"SELECT id, markdown, markdown_meta FROM crawled_pages WHERE id::text IN ({placeholders})",
                    page_ids,
                )
                md_map = {}
                for row in cur.fetchall():
                    md_map[str(row[0])] = {"markdown": row[1], "markdown_meta": row[2]}

            for item in data:
                pid = item.get("page_id")
                if pid and pid in md_map:
                    item["markdown"] = md_map[pid]["markdown"]
                    item["markdown_meta"] = md_map[pid]["markdown_meta"]

    avg_confidence = (
        sum(d.get("confidence", d.get("score", 0)) for d in data) / len(data)
        if data else 0.0
    )

    return PhantomResponse(
        data=data,
        sources=[],
        confidence=round(avg_confidence, 3),
        freshness=None,
        related=[],
    )
