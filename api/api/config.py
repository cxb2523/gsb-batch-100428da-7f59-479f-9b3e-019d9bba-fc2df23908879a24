import os


class Config:
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    REDIS_STREAM_NAME: str = os.getenv("REDIS_STREAM_NAME", "phantombot:crawled")

    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "phantombot")
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "phantombot")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "localdev")

    QDRANT_HOST: str = os.getenv("QDRANT_HOST", "localhost")
    QDRANT_PORT: int = int(os.getenv("QDRANT_PORT", "6335"))
    QDRANT_COLLECTION: str = os.getenv("QDRANT_COLLECTION", "phantombot")

    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", "8000"))
    API_KEY: str = os.getenv("API_KEY", "")  # Empty = auth disabled

    @classmethod
    def postgres_dsn(cls) -> str:
        return (
            f"host={cls.POSTGRES_HOST} port={cls.POSTGRES_PORT} "
            f"dbname={cls.POSTGRES_DB} user={cls.POSTGRES_USER} "
            f"password={cls.POSTGRES_PASSWORD}"
        )
