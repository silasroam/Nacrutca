"""
Regression test for the "Заказ устарел" bug.

A freshly created order must have:
    - payment_status == "pending"
    - expires_at ~= created_at + 30 minutes

and must NOT be treated as expired when a currency is selected immediately after
creation. Steps mirror the real callback flow (currency selection).
"""
import asyncio
from datetime import datetime, timedelta, timezone

from bot.database.repository import Repository
from bot.services import pricing
from bot.handlers import payment as payment_h


async def main() -> None:
    r = Repository("sqlite+aiosqlite:///:memory:")
    await r.init()
    await pricing.seed_default_services(r)

    for _ in range(4):
        likes = next(
            s for s in await r.list_services("tiktok") if s.service_slug == "likes"
        )
        order = await r.create_order(
            tg_user_id=555, username="buyer", platform="tiktok", service=likes,
            quantity=1000, target_url="https://tiktok.com/@x/video/1",
        )
        assert order.payment_status == "pending", order.payment_status
        assert order.order_status == "pending"

        created = payment_h._ensure_aware_utc(order.created_at)
        expires = payment_h._ensure_aware_utc(order.expires_at)
        diff = expires - created
        print(f"[expiry] order {order.order_id} diff_min={diff.seconds / 60:.1f}")
        assert timedelta(minutes=29) <= diff <= timedelta(minutes=31), diff

        # A brand-new pending order must never be "expired".
        assert payment_h._order_expired(order) is False, (
            f"fresh order {order.order_id} misreported as expired"
        )
        # owner/pending/expiry gate used by the currency handler
        assert order.telegram_user_id == 555
        assert order.payment_status == "pending"
        assert payment_h._order_expired(order) is False

    # Now force-expire an order: deadline in the past -> must be expired.
    expires_order = await r.create_order(
        tg_user_id=555, username="buyer", platform="tiktok", service=likes,
        quantity=1000, target_url="https://tiktok.com/@x/video/2",
    )
    await r.save_crypto_invoice(
        order_id=expires_order.order_id, currency="TON", crypto_amount="1.0",
        exchange_rate="119.87", wallet_address="addr",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    fresh = await r.get_order_by_id(expires_order.order_id)
    assert payment_h._order_expired(fresh) is True, "past deadline must be expired"
    print("Expired order correctly flagged")

    await r.close()
    print("ORDER EXPIRY TEST PASSED")


if __name__ == "__main__":
    asyncio.run(main())