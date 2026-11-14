import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_redis_client, get_pg_connection, verify_api_key
from api.config import Config
from api.models import CrawlRequest, PhantomResponse

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.post("/crawl", status_code=202, response_model=PhantomResponse)
def request_crawl(req: CrawlRequest):
    parsed = urlparse(req.url)
    if not parsed.scheme or not parsed.netloc:
        raise HTTPException(status_code=422, detail="Invalid URL: must include scheme and host")

    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=422, detail="Invalid URL: only http/https supported")

    job_id = str(uuid.uuid4())

    # Record the job in PostgreSQL
    pg = get_pg_connection()
    with pg.cursor() as cur:
        cur.execute(
            "INSERT INTO crawl_jobs (id, url, status) VALUES (%s, %s, 'pending') RETURNING id",
            (job_id, req.url),
        )
        cur.fetchone()

    # Publish crawl request to Redis for the Go crawler to pick up
    rdb = get_redis_client()
    rdb.xadd(
        "phantombot:crawl_requests",
        {
            "url": req.url,
            "max_depth": str(req.max_depth),
            "job_id": job_id,
        },
    )

    return PhantomResponse(
        data={"job_id": job_id, "url": req.url, "status": "pending"},
        sources=[],
        confidence=0.0,
        freshness=None,
        related=[],
    )
