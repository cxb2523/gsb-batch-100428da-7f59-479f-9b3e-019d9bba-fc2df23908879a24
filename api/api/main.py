from fastapi import FastAPI

from api.routes import crawl, query, search, page, scrape, sitemap

app = FastAPI(
    title="Phantombot",
    description="Agent-queryable web knowledge API",
    version="0.1.0",
)

app.include_router(crawl.router, tags=["crawl"])
app.include_router(query.router, tags=["query"])
app.include_router(search.router, tags=["search"])
app.include_router(page.router, tags=["page"])
app.include_router(scrape.router, tags=["scrape"])
app.include_router(sitemap.router, tags=["map"])


@app.get("/health")
def health():
    return {"status": "ok"}
