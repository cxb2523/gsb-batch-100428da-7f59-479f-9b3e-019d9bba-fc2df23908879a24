import os


class Config:
    MCP_TRANSPORT: str = os.getenv("MCP_TRANSPORT", "stdio")
    MCP_HTTP_HOST: str = os.getenv("MCP_HTTP_HOST", "0.0.0.0")
    MCP_HTTP_PORT: int = int(os.getenv("MCP_HTTP_PORT", "8001"))

    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "phantombot")
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "phantombot")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "localdev")

    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")

    QDRANT_HOST: str = os.getenv("QDRANT_HOST", "localhost")
    QDRANT_PORT: int = int(os.getenv("QDRANT_PORT", "6335"))
    QDRANT_COLLECTION: str = os.getenv("QDRANT_COLLECTION", "phantombot")

    @classmethod
    def postgres_dsn(cls) -> str:
        return (
            f"host={cls.POSTGRES_HOST} port={cls.POSTGRES_PORT} "
            f"dbname={cls.POSTGRES_DB} user={cls.POSTGRES_USER} "
            f"password={cls.POSTGRES_PASSWORD}"
        )
