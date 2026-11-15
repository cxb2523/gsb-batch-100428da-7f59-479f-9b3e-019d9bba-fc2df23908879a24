"""
User management routes — admin-only: create and delete staff users.
"""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request):
    """Render the user management page. Admin only."""
    raise NotImplementedError("Proprietary implementation")


@router.post("/users/create")
async def create_user(request: Request):
    """
    Create a new staff user in the current organization.
    Admin only. Staff users share the same dashboard and account access.
    """
    raise NotImplementedError("Proprietary implementation")


@router.post("/users/{user_id}/delete")
async def delete_user(request: Request, user_id: int):
    """
    Delete a staff user from the current organization.
    Admin only. Admin cannot delete their own account.
    """
    raise NotImplementedError("Proprietary implementation")
