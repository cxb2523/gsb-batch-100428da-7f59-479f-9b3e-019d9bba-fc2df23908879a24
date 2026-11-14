from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def _run_sqlite_migrations():
    """Add columns introduced after the first release to pre-existing databases.

    SQLAlchemy's ``create_all`` only creates missing tables; it never alters an
    existing table, so new Page columns are added here explicitly.
    """
    if not settings.DATABASE_URL.startswith("sqlite"):
        return

    async with engine.begin() as conn:
        existing = {row[1] for row in await conn.exec_driver_sql("PRAGMA table_info(pages)")}
        if not existing:
            return  # table does not exist yet; create_all will build it with the new schema
        if "outcome" not in existing:
            await conn.exec_driver_sql(
                "ALTER TABLE pages ADD COLUMN outcome VARCHAR(16) "
                "NOT NULL DEFAULT 'ok'"
            )
        if "error_message" not in existing:
            await conn.exec_driver_sql(
                "ALTER TABLE pages ADD COLUMN error_message TEXT"
            )


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _run_sqlite_migrations()


async def get_db():
    async with async_session() as session:
        yield session
