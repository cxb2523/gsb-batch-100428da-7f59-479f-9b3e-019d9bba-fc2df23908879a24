"""
Settings routes — settings page and all configuration management endpoints.
"""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Render the settings page with current config, accounts, and license info."""
    raise NotImplementedError("Proprietary implementation")


@router.post("/settings/save")
async def save_settings(request: Request):
    """
    Save all settings: sync schedule, date range, dashboard defaults,
    automation rules, Telegram config, license key, and org name.
    Reschedules sync jobs if the sync config changed.
    """
    raise NotImplementedError("Proprietary implementation")


@router.post("/settings/add-account")
async def add_account(request: Request):
    """
    Add a new Meta ad account to the current tenant.
    Enforces the license-based account limit.
    """
    raise NotImplementedError("Proprietary implementation")


@router.post("/settings/update-account/{account_id}")
async def update_account(request: Request, account_id: str):
    """Update account name, token, sheet ID, worksheet names, or API version."""
    raise NotImplementedError("Proprietary implementation")


@router.post("/settings/delete-account/{account_id}")
async def delete_account(request: Request, account_id: str):
    """
    Delete an ad account after password confirmation.
    Also removes the account's cache file.
    """
    raise NotImplementedError("Proprietary implementation")


@router.post("/settings/upload-google-key")
async def upload_google_key(request: Request):
    """Accept a Google service account JSON file upload and save it to the tenant directory."""
    raise NotImplementedError("Proprietary implementation")


@router.post("/settings/validate-token")
async def validate_token(request: Request):
    """
    Call the Meta API with the provided token and ad account ID.
    Returns JSON with valid (bool) and message (str).
    """
    raise NotImplementedError("Proprietary implementation")


@router.post("/settings/send-test-telegram")
async def send_test_telegram(request: Request):
    """Send a test message to the configured Telegram chat."""
    raise NotImplementedError("Proprietary implementation")


@router.get("/settings/check-license")
async def check_license(request: Request):
    """Return JSON with the current license status, client name, and allowed accounts."""
    raise NotImplementedError("Proprietary implementation")
