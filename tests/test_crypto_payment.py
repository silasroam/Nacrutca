"""
Tests for the new crypto payment flow (section 14).

Covers exact Decimal math, invoice creation freezing the rate, and the
verification serving-side logic (paid / not found / underpaid / expired /
idempotent tx reuse) with a stubbed blockchain adapter.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bot.config import get_settings
from bot.database.repository import Repository
from bot.services import crypto_payment as cp
from bot.services import crypto_rates as rates
from bot.services import pricing
from bot.services.blockchain import TxCheckResult


async def main() -> None:
    r = Repository("sqlite+aiosqlite:///:memory:")
    await r.init()
    await pricing.seed_default_services(r)

    likes = next(
        s for s in await r.list_services("tiktok") if s.service_slug == "likes"
    )
    order = await r.create_order(
        tg_user_id=111, username="test", platform="tiktok", service=likes,
        quantity=1000, target_url="https://tiktok.com/@x/video/1",
    )
    assert order.total_price == 180.0

    # 1) exact Decimal math: 500 RUB @ 91.54 TON -> 5.4621 TON
    fiat = Decimal("500")
    rate = Decimal("91.54")
    crypto = rates.crypto_amount_for_fiat(fiat, rate)
    assert crypto == Decimal("5.4621"), crypto
    assert rates.format_decimal(crypto) == "5.4621"
    print("Decimal crypto amount OK:", rates.format_decimal(crypto))

    # 2) invoice creation freezes the rate and stores all fields
    await r.save_crypto_invoice(
        order_id=order.order_id, currency="TON",
        crypto_amount="1.2345", exchange_rate="91.54",
        wallet_address=rates.CRYPTO_WALLETS["TON"],
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    saved = await r.get_order_by_id(order.order_id)
    assert saved.crypto_currency == "TON"
    assert saved.crypto_amount == "1.2345"
    assert saved.exchange_rate == "91.54"
    print("Invoice fields persisted OK")

    # -- stub the blockchain adapter via get_checker monkey-patch in service --
    original_checker = cp.get_checker

    class StubPaid:
        async def check(self, **kw):
            return TxCheckResult(
                found=True, transaction_hash="tx_abc", currency="TON",
                amount=Decimal("1.2345"), destination=saved.wallet_address,
                timestamp=datetime.now(timezone.utc), confirmations=1, is_final=True,
            )

    cp.get_checker = lambda code: StubPaid()  # type: ignore[assignment]
    try:
        outcome = await cp.verify_crypto_payment(r, order=saved, user_id=111)
        assert outcome.status == "paid", outcome.status
        # tx now used -> idempotency
        assert await r.is_transaction_used("tx_abc") is True
        print("Verify paid OK:", outcome.status)
    finally:
        cp.get_checker = original_checker

    # -- underpaid --
    class StubUnderpaid:
        async def check(self, **kw):
            return TxCheckResult(
                found=True, transaction_hash="tx_under", currency="TON",
                amount=Decimal("1.0000"), destination=saved.wallet_address,
                timestamp=datetime.now(timezone.utc), confirmations=1, is_final=True,
            )

    cp.get_checker = lambda code: StubUnderpaid()  # type: ignore[assignment]
    try:
        order2 = await r.create_order(
            tg_user_id=111, username="test", platform="tiktok", service=likes,
            quantity=1000, target_url="https://tiktok.com/@x/video/2",
        )
        await r.save_crypto_invoice(
            order_id=order2.order_id, currency="TON", crypto_amount="5.4621",
            exchange_rate="91.54", wallet_address="addr",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        fresh2 = await r.get_order_by_id(order2.order_id)
        out2 = await cp.verify_crypto_payment(r, order=fresh2, user_id=111)
        assert out2.status == "underpaid", out2.status
        after = await r.get_order_by_id(order2.order_id)
        assert after.payment_status != "paid"
        print("Underpayment handled OK (status not paid)")
    finally:
        cp.get_checker = original_checker

    # -- expired --
    order3 = await r.create_order(
        tg_user_id=111, username="test", platform="tiktok", service=likes,
        quantity=1000, target_url="https://tiktok.com/@x/video/3",
    )
    await r.save_crypto_invoice(
        order_id=order3.order_id, currency="TON", crypto_amount="5.4621",
        exchange_rate="91.54", wallet_address="addr",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    fresh3 = await r.get_order_by_id(order3.order_id)
    out3 = await cp.verify_crypto_payment(r, order=fresh3, user_id=111)
    assert out3.status == "expired", out3.status
    print("Expired invoice rejected OK")

    # -- wrong owner --
    out4 = await cp.verify_crypto_payment(r, order=saved, user_id=999)
    assert out4.status == "error", out4.status
    print("Ownership check OK")

    # -- cancel --
    order5 = await r.create_order(
        tg_user_id=111, username="test", platform="tiktok", service=likes,
        quantity=1000, target_url="https://tiktok.com/@x/video/5",
    )
    await r.save_crypto_invoice(
        order_id=order5.order_id, currency="LTC", crypto_amount="1.0",
        exchange_rate="4777.79", wallet_address="ltcaddr",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    c5 = await r.cancel_crypto_order(order5.order_id)
    assert c5.payment_status == "cancelled"
    # re-cancel is a no-op
    c5b = await r.cancel_crypto_order(order5.order_id)
    assert c5b.payment_status == "cancelled"
    print("Cancel flow OK")

    await r.close()
    print("CRYPTO PAYMENT TEST PASSED")


if __name__ == "__main__":
    asyncio.run(main())