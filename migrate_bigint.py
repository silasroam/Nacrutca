"""One-off migration: widen telegram_user_id columns from INTEGER to BIGINT.

Telegram user IDs exceed the 32-bit signed int range (max 2147483647); real IDs
like 7969090536 overflow INTEGER and fail with:
    DataError: value out of int32 range
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from bot.database.repository import _normalize_database_url  # noqa: E402


def _url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
        url = os.getenv("DATABASE_URL", "").strip()
    return _normalize_database_url(url or "sqlite+aiosqlite:///./traffic_bot.db")


TABLES = ("users", "orders", "payments")


async def main() -> None:
    engine = create_async_engine(_url(), echo=False)
    try:
        for t in TABLES:
            # Idempotent: only ALTER if the column is still INTEGER/BIGINT-mismatched.
            async with engine.begin() as conn:
                res = await conn.execute(
                    text(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_name = :t AND column_name = 'telegram_user_id'"
                    ),
                    {"t": t},
                )
                row = res.first()
                cur = row[0] if row else None
                if cur == "bigint":
                    print(f"[skip] {t}.telegram_user_id already bigint")
                    continue
                await conn.execute(
                    text(
                        f"ALTER TABLE {t} ALTER COLUMN telegram_user_id TYPE BIGINT"
                    )
                )
                print(f"[ok] {t}.telegram_user_id -> BIGINT (was {cur})")
    finally:
        await engine.dispose()


asyncio.run(main())