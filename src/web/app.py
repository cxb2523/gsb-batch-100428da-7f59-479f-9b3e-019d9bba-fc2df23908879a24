"""
FastAPI application — registers middleware, mounts static files,
includes all routers, and handles startup/shutdown lifecycle events.
"""

import os
import logging
import logging.handlers
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from dotenv import load_dotenv

load_dotenv()

Path("logs").mkdir(exist_ok=True)
_log_handler = logging.handlers.RotatingFileHandler(
    "logs/error.log", maxBytes=500_000, backupCount=3
)
logging.basicConfig(
    handlers=[_log_handler, logging.StreamHandler()],
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

app = FastAPI(title="Meta Ads Intelligence Platform", docs_url=None, redoc_url=None)


async def _auth_middleware(request: Request, call_next):
    """
    Authentication and tenant context middleware.

    Checks session state for every non-public request. Redirects to /login
    if no session is present. Sets the tenant ContextVar for the duration
    of the request. Redirects incomplete profiles to /setup.

    Note:
        Public path list and session validation logic are proprietary and
        not included in this public version.
    """
    raise NotImplementedError("Proprietary implementation")


app.add_middleware(BaseHTTPMiddleware, dispatch=_auth_middleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "change-me-in-production"),
    max_age=86400,
)

app.mount("/static", StaticFiles(directory="web/static"), name="static")

from web.routes.auth      import router as auth_router
from web.routes.dashboard import router as dashboard_router
from web.routes.settings  import router as settings_router
from web.routes.sync      import router as sync_router
from web.routes.setup     import router as setup_router
from web.routes.users     import router as users_router
from web.routes.api       import router as api_router

app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(settings_router)
app.include_router(sync_router)
app.include_router(setup_router)
app.include_router(users_router)
app.include_router(api_router)


@app.on_event("startup")
async def startup():
    """
    Initialize main database, run auto-migration for legacy installations,
    and start the APScheduler background scheduler.
    """
    from db.main_db import init_main_db
    init_main_db()
    try:
        from core.scheduler import start_scheduler
        start_scheduler(app)
    except Exception as e:
        logging.error(f"Scheduler startup failed: {e}")


@app.on_event("shutdown")
async def shutdown():
    """Shut down the background scheduler gracefully."""
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
