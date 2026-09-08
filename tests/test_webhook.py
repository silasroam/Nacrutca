"""Verifies the Application wiring (all handler modules register) and the
idempotent webhook confirmation path end-to-end.
"""
import asyncio
import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN")

from bot.database.repository import Repository
from bot.database.models import Service
from bot.services import pricing
from bot.services.payments import provider_for, webhook_confirm_order
from bot.main import build_application


async def main() -> None:
    r = Repository("sqlite+aiosqlite:///:memory:")
    await r.init()
    await pricing.seed_default_services(r)

    app = build_application(r)
    print("Application built OK; handlers registered:", len(app.handlers))

    # end-to-end: place order, then simulate a crypto provider webhook confirm
    likes = next(
        s for s in await r.list_services("tiktok") if s.service_slug == "likes"
    )

    # Start a crypto payment, which creates a pending Payment row + hint.
    provider = provider_for("crypto", r)
    order = await r.create_order(
        tg_user_id=1, username="buyer", platform="tiktok", service=likes,
        quantity=1000, target_url="https://tiktok.com/@a/video/9",
    )
    ref, hint = await provider.start_payment(
        telegram_user_id=1, order=order, amount_float=order.total_price,
    )
    print("provider hint:", hint.splitlines()[0])

    # Simulate the provider webhook for the returned payment_ref.
    res = await provider.handle_webhook({"payment_id": ref, "order_id": order.order_id})
    print("webhook result:", res, "duplicate:", res.is_duplicate)
    assert res.ok and res.status == "paid"

    # Re-send the same webhook (idempotent) -> no duplicate/error.
    res2 = await provider.handle_webhook(
        {"payment_id": ref, "order_id": order.order_id}
    )
    print("repeat webhook:", res2)
    assert res2.ok

    order_after = await r.get_order_by_id(order.order_id)
    assert order_after.payment_status == "paid"
    assert order_after.order_status == "processing"
    print("End-to-end webhook confirmation OK")

    await r.close()


asyncio.run(main())
print("APP+WEBHOOK TEST PASSED")