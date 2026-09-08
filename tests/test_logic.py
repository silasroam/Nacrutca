"""Smoke test: exercises repository, pricing, order creation, and idempotent
payment confirmation logic against an in-memory SQLite database.
"""
import asyncio

from bot.config import get_settings
from bot.database.repository import (
    DuplicatePaymentError,
    OrderNotFoundError,
    Repository,
)
from bot.services import pricing
from bot.services.orders import ValidationError, validate_quantity


async def main() -> None:
    r = Repository("sqlite+aiosqlite:///:memory:")
    await r.init()
    await pricing.seed_default_services(r)

    print("== services ==")
    for plat in ("telegram", "tiktok", "instagram", "youtube"):
        svcs = await r.list_services(plat)
        print(plat, [(s.service_slug, s.price_per_1000) for s in svcs])

    settings = get_settings()
    likes = next(
        s for s in await r.list_services("tiktok") if s.service_slug == "likes"
    )

    # valid quantity
    q = validate_quantity("2500", settings, likes)
    assert q == 2500
    # invalid inputs
    for bad in ("abc", "-5", "0", "50", "999999999999"):
        try:
            validate_quantity(bad, settings, likes)
            raise AssertionError(f"should have failed: {bad}")
        except ValidationError:
            pass
    print("quantity validation OK")

    # order creation
    o = await r.create_order(
        tg_user_id=111, username="test", platform="tiktok", service=likes,
        quantity=q, target_url="https://tiktok.com/@x/video/1",
    )
    print("order", o.order_id, "total", o.total_price, "status", o.order_status)
    assert o.total_price == 450.0  # 2500 * 180 / 1000

    # admin overrides price -> server re-reads it (section 23)
    await r.update_service_price(likes.id, 200.0)
    o2 = await r.create_order(
        tg_user_id=111, username="test", platform="tiktok", service=likes,
        quantity=1000, target_url="https://tiktok.com/@x/video/2",
    )
    assert o2.total_price == 200.0
    print("server-side price re-read OK")

    # idempotent payment confirmation (section 23)
    # first confirm creates a payment and marks the order paid + processing
    res = await r.get_payment("__none__")
    assert res is None
    p = await r.create_payment(
        order_id=o.order_id, tg_user_id=111, method="crypto",
        provider="stub", amount=o.total_price,
    )
    c = await r.confirm_payment(p.payment_id, o.order_id)
    assert c.status == "paid"
    order_after = await r.get_order_by_id(o.order_id)
    assert order_after.payment_status == "paid"
    assert order_after.order_status == "processing"  # auto to processing
    # repeat confirm is a no-op (idempotent)
    c2 = await r.confirm_payment(p.payment_id, o.order_id)
    assert c2.status == "paid"
    print("idempotent payment confirm OK")

    # duplicate payment for an already-paid order must be rejected
    p2 = await r.create_payment(
        order_id=o.order_id, tg_user_id=111, method="stars",
        provider="stub", amount=o.total_price,
    )
    try:
        await r.confirm_payment(p2.payment_id, o.order_id)
        raise AssertionError("expected DuplicatePaymentError")
    except DuplicatePaymentError:
        pass
    print("duplicate-payment guard OK")

    # stats
    stats = await r.user_stats(111)
    print("user stats:", stats)
    assert stats["total_orders"] == 2
    sales = await r.sales_stats()
    print("sales:", sales)

    await r.close()
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    asyncio.run(main())