"""
One-time migration script for legacy single-user installations.

Converts a legacy .env / file-based setup into the multi-tenant database
structure. Run this once when upgrading from a single-user version.

Note:
    Migration logic is proprietary and not included in this public version.
    The app also performs auto-migration on startup if the database is empty.
"""


def migrate() -> None:
    """
    Migrate a legacy installation to the multi-tenant structure.

    Reads ADMIN_USERNAME and ADMIN_PASSWORD from the environment, creates the
    first Organization and User in the main database, copies existing
    settings.json and google-api-key.json into the tenant directory.

    Note:
        Migration implementation is proprietary and not included in this
        public version.
    """
    raise NotImplementedError("Proprietary implementation")


if __name__ == "__main__":
    migrate()
