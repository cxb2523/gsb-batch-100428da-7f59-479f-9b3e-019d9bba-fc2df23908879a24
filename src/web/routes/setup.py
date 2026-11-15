"""
Setup routes — post-Google-OAuth profile completion flow.

New users who sign in with Google are redirected here to complete their
profile (choose a username and display name) before accessing the app.
"""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    """Render the profile setup page for new Google OAuth users."""
    raise NotImplementedError("Proprietary implementation")


@router.post("/setup")
async def setup_submit(request: Request):
    """
    Save username and display name, mark profile as complete,
    and redirect to the dashboard.
    """
    raise NotImplementedError("Proprietary implementation")
