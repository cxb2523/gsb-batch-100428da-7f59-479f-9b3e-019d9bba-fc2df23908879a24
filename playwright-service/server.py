"""Playwright render sidecar — renders JS-heavy pages and returns HTML."""
import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Playwright imports
from playwright.async_api import async_playwright, Browser

RENDER_TIMEOUT = int(os.getenv("RENDER_TIMEOUT_MS", "15000"))
PORT = int(os.getenv("PLAYWRIGHT_PORT", "3100"))

browser: Browser | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global browser
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    yield
    await browser.close()
    await pw.stop()


app = FastAPI(
    title="Phantombot Playwright Sidecar",
    description="Renders JS-heavy pages and returns HTML",
    version="0.1.0",
    lifespan=lifespan,
)


class RenderRequest(BaseModel):
    url: str
    timeout_ms: int = RENDER_TIMEOUT
    wait_until: str = "networkidle"  # load, domcontentloaded, networkidle


class RenderResponse(BaseModel):
    url: str
    html: str
    status_code: int
    title: str


@app.get("/health")
async def health():
    return {"status": "ok", "browser": browser is not None}


@app.post("/render", response_model=RenderResponse)
async def render_page(req: RenderRequest):
    if browser is None:
        raise HTTPException(status_code=503, detail="Browser not initialized")

    page = await browser.new_page()
    try:
        response = await page.goto(
            req.url,
            timeout=req.timeout_ms,
            wait_until=req.wait_until,
        )

        if response is None:
            raise HTTPException(status_code=502, detail="No response from page")

        html = await page.content()
        title = await page.title()

        return RenderResponse(
            url=req.url,
            html=html,
            status_code=response.status,
            title=title,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Render failed: {str(e)}")
    finally:
        await page.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
