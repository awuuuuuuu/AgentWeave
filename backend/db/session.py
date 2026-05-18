from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

_DATABASE_URL = settings.database_url

engine = create_async_engine(
    _DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=1800,
    connect_args={"ssl": False},
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    session = SessionLocal()
    try:
        yield session
    except Exception:
        try:
            await session.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            await session.close()
        except Exception:
            # 底层连接已断开（如长时 SSE 期间 asyncpg 超时）时忽略 close 错误
            pass