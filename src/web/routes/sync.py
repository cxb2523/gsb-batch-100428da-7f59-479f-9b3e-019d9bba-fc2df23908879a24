"""
Sync routes — manual sync trigger for the current tenant.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/sync/now")
async def sync_now(request: Request):
    """
    Trigger an immediate sync for all accounts in the current tenant.

    Sets the tenant context and calls run_all_accounts(). Returns JSON
    with per-account results.

    Note:
        Sync orchestration is handled by core/sync_engine.py.
    """
    raise NotImplementedError("Proprietary implementation")
