"""Storage layer — DuckDB over Parquet."""

from crawlforge.storage.schema import SCHEMA_VERSION, init_database

__all__ = ["SCHEMA_VERSION", "init_database"]
