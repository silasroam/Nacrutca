#!/usr/bin/env python3
"""
Database initialisation script.

Creates (and optionally drops) all tables in the database referenced by
``DATABASE_URL``.

Usage:
    python init_db.py                # create tables (idempotent)
    python init_db.py --drop         # drop ALL existing tables first, then create
    DATABASE_URL=postgresql+asyncpg://... python init_db.py

The URL is read from the ``DATABASE_URL`` env var if set; otherwise it falls
back to ``.env`` (via python-dotenv) or to the default SQLite file. It reuses
the same async driver normalisation as ``bot.database.repository`` so a bare
``postgresql://`` value is rewritten to ``postgresql+asyncpg://``.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Ensure the project root is on sys.path when run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from bot.database.models import Base  # noqa: E402  (imports all ORM models)


def _resolve_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url and url.strip():
        return url.strip()
    # Fallback: load .env at the project root (mirrors bot/config.py).
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
        url = os.getenv("DATABASE_URL", "").strip()
    except Exception:  # pragma: no cover - dotenv unavailable
        url = ""
    return url or "sqlite+aiosqlite:///./traffic_bot.db"


def normalize_url(url: str) -> str:
    """Reuse the async-driver rewrite so a bare postgresql:// works with async."""
    # Quick, local-only mirror of Repository._normalize_database_url to keep this
    # script dependency-light (it does not construct a Repository).
    if url.startswith("postgres://") or url.startswith("postgresql://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    url = url.replace("&channel_binding=require", "")
    url = url.replace("?channel_binding=require", "?")
    url = url.replace("?channel_binding=require&", "?")
    url = url.replace("?sslmode=require", "?ssl=require").replace(
        "?sslmode=prefer", "?ssl=prefer"
    )
    return url


async def _run(url: str, drop: bool) -> None:
    engine = create_async_engine(url, echo=False)
    try:
        if drop:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            print(f"[drop] DROPPED all tables on {engine.dialect.name}")

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print(f"[ok] Created all tables on {engine.dialect.name}")
        print("Tables:", ", ".join(sorted(Base.metadata.tables.keys())))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create DB schema for the bot.")
    parser.add_argument(
        "--drop",
        action="store_true",
        help="Drop existing tables before creating (destructive!).",
    )
    args = parser.parse_args()

    url = normalize_url(_resolve_database_url())
    print(f"Using database: {url.split('@')[-1] if '@' in url else url}")
    asyncio.run(_run(url, args.drop))


if __name__ == "__main__":
    main()