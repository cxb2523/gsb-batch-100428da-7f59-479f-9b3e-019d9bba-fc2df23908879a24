"""
Tenant database management.

Each organization gets its own SQLite database at data/tenants/db_org_{id}.db
and a data directory at data/tenants/{id}/ for encrypted settings, cache, and
Google API keys.

The active organization is stored in a ContextVar so that config_manager and
other services can resolve the correct paths without explicit parameter passing.
"""

import contextvars
from pathlib import Path

from sqlalchemy import create_engine, text

_current_org_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "org_id", default=None
)

_TENANT_ROOT = Path("data/tenants")


def set_current_org(org_id: int | None) -> None:
    """
    Set the active organization for the current execution context.

    Called by auth middleware at the start of each authenticated request.
    Also called by the scheduler before running syncs for each organization.

    Args:
        org_id: Organization ID, or None to clear the context.
    """
    _current_org_id.set(org_id)


def get_current_org() -> int | None:
    """
    Return the active organization ID for the current execution context.

    Returns:
        Integer org ID, or None if no tenant context is active.
    """
    return _current_org_id.get()


def tenant_data_dir(org_id: int | None = None) -> Path:
    """
    Return the data directory path for an organization, creating it if needed.

    Args:
        org_id: Explicit org ID. Uses ContextVar if None.

    Returns:
        Path object for data/tenants/{org_id}/.
    """
    raise NotImplementedError("Proprietary implementation")


def tenant_db_path(org_id: int | None = None) -> Path:
    """
    Return the SQLite database file path for an organization.

    Args:
        org_id: Explicit org ID. Uses ContextVar if None.

    Returns:
        Path object for data/tenants/db_org_{org_id}.db.
    """
    raise NotImplementedError("Proprietary implementation")


def create_tenant_db(org_id: int) -> str:
    """
    Provision a new tenant SQLite database and initialize its base schema.

    Called when a new organization is created on registration.

    Args:
        org_id: The new organization's ID.

    Returns:
        The db_name string stored in the tenant_databases table.
    """
    raise NotImplementedError("Proprietary implementation")


def get_tenant_engine(org_id: int | None = None):
    """
    Return a SQLAlchemy engine connected to the tenant's database.

    Args:
        org_id: Explicit org ID. Uses ContextVar if None.

    Returns:
        SQLAlchemy Engine instance.
    """
    raise NotImplementedError("Proprietary implementation")
