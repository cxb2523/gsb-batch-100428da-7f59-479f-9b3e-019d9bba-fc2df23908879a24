"""
Super-admin API routes — health and status endpoints for system monitoring.
Requires X-API-Key header matching ADMIN_API_KEY from environment.
"""

from fastapi import APIRouter, Request, Header, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/api/ping")
async def ping():
    """Health check endpoint. Returns {'status': 'ok'}. No auth required."""
    return {"status": "ok"}


@router.get("/api/status")
async def status(request: Request, x_api_key: str = Header(default="")):
    """
    Return system status: organization count, user count, scheduler state.
    Requires a valid API key in the X-API-Key header.

    Note:
        API key validation logic is proprietary and not included in this
        public version.
    """
    raise NotImplementedError("Proprietary implementation")
