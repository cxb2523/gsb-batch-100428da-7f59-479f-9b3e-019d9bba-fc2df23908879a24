"""
Main database — stores users, organizations, and tenant DB mappings.
All ad and settings data lives in per-tenant databases managed by tenant_manager.py.
"""

from pathlib import Path
from datetime import datetime

from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey
)
from sqlalchemy.orm import declarative_base, sessionmaker

_DB_PATH = "data/main.db"
engine = create_engine(
    f"sqlite:///{_DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


class Organization(Base):
    """Tenant organization. Created on user registration."""
    __tablename__ = "organizations"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    name       = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    """User account. Belongs to exactly one organization."""
    __tablename__ = "users"
    id                  = Column(Integer, primary_key=True, autoincrement=True)
    email               = Column(String, unique=True, nullable=True)
    username            = Column(String, unique=True, nullable=False)
    password_hash       = Column(String, nullable=True)
    name                = Column(String, nullable=False, default="")
    role                = Column(String, nullable=False, default="staff")
    organization_id     = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    is_profile_complete = Column(Boolean, nullable=False, default=False)
    google_id           = Column(String, unique=True, nullable=True)
    created_at          = Column(DateTime, default=datetime.utcnow)


class TenantDatabase(Base):
    """Maps an organization to its tenant SQLite database filename."""
    __tablename__ = "tenant_databases"
    organization_id = Column(Integer, ForeignKey("organizations.id"), primary_key=True)
    db_name         = Column(String, nullable=False)


def init_main_db() -> None:
    """Create all tables if they do not exist. Called on application startup."""
    Path("data").mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


def open_db():
    """Return a raw SQLAlchemy session. Caller is responsible for closing it."""
    return SessionLocal()
