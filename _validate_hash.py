"""Validate public_hash generation + order_code helper (test-only, in-memory)."""
import asyncio

from bot.database.repository import Repository
from bot.services import pricing
from bot.services.orders import order_code


async def main():
    r = Repository("sqlite+aiosqlite:///:memory:")
    await r.init()
    await pricing.seed_default_services(r)

    likes = next(
        s for s in await r.list_services("tiktok") if s.service_slug == "likes"
    )

    o1 = await r.create_order(
        tg_user_id=1, username="a", platform="tiktok", service=likes,
        quantity=1000, target_url="https://tiktok.com/@x/video/1",
    )
    o2 = await r.create_order(
        tg_user_id=2, username="b", platform="tiktok", service=likes,
        quantity=2000, target_url="https://tiktok.com/@x/video/2",
    )

    print("o1 public_hash:", o1.public_hash, "| len:", len(o1.public_hash))
    print("o2 public_hash:", o2.public_hash)
    print("order_code(o1):", order_code(o1))
    print("order_code(o2):", order_code(o2))

    assert o1.public_hash and len(o1.public_hash) == 12, o1.public_hash
    assert o2.public_hash and len(o2.public_hash) == 12, o2.public_hash
    assert o1.public_hash != o2.public_hash, "hashes must differ"
    assert order_code(o1) == o1.public_hash
    assert "#" not in order_code(o1), "should not expose numeric id"
    assert "#" not in order_code(o2)

    # Reload from DB to confirm the hash persisted.
    fresh = await r.get_order_by_id(o1.order_id)
    assert fresh.public_hash == o1.public_hash
    print("persisted public_hash OK:", fresh.public_hash)

    # Legacy row without a hash -> order_code falls back to #id.
    class Legacy:
        public_hash = ""
        order_id = 42
    assert order_code(Legacy()) == "#42"
    print("legacy fallback OK")

    print("PUBLIC HASH VALIDATION OK")
    await r.close()


asyncio.run(main())