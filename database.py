import os

from sqlalchemy import text
from sqlmodel import SQLModel, create_engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'freelancermap.db')}")

engine = create_engine(DATABASE_URL)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    # ponytail: no migration framework for a single-file sqlite project this
    # size — a guarded ALTER TABLE covers adding a column to an existing db.
    # Upgrade to Alembic if more than one such migration piles up.
    with engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE project ADD COLUMN source TEXT"))
        except Exception:
            pass  # column already exists
