"""
Tests for the Telegram Stars payment flow (official Telegram Payments).

Covers: price calc, back-end pre-checkout gating, and the ONLY-paid-via
successful_payment resolution with idempotency and amount/ownership checks.
"""
import asyncio
from decimal import Decimal

from bot.database.repository import Repository
from bot.services import pricing
from bot.services import stars as stars_svc
from bot.services import stars_payment as sp


async def main() -> None:
    # --- price calc (fixed 130 RUB = 100 Stars) ---
    cases = {"130": 100, "260": 200, "500": 385, "1000": 770}
    for fiat, expected in cases.items():
        got = stars_svc.fiat_to_stars(Decimal(fiat))
        assert got == expected, (fiat, got, expected)
        print(f"stars {fiat}₽ -> {got} ⭐")
    assert str(stars_svc.rub_per_star()) == "1.30"

    r = Repository("sqlite+aiosqlite:///:memory:")
    await r.init()
    await pricing.seed_default_services(r)
    likes = next(s for s in await r.list_services("tiktok") if s.service_slug == "likes")
    order = await r.create_order(
        tg_user_id=111, username="buyer", platform="tiktok", service=likes,
        quantity=1000, target_url="https://tiktok.com/@x/video/1",
    )
    stars = stars_svc.fiat_to_stars(Decimal(str(order.total_price)))
    await r.set_stars_amount(order.order_id, stars)
    print("order total₽:", order.total_price, "stars:", stars)
    assert stars == 139  # 180 ₽ -> ceil(180*100/130)=139 (rounds up from 138.46)

    # --- payload round-trip ---
    payload = sp.payload_for_order(order.order_id)
    assert sp.order_id_from_payload(payload) == order.order_id
    print("payload round-trip OK")

    # --- pre-checkout: ok ---
    dec = await sp.handle_pre_checkout(r, order_id=order.order_id,
                                       user_id=111, stars_amount=stars)
    assert dec.ok is True, dec.message
    print("pre-checkout ok=True (valid order)")

    # --- pre-checkout: wrong amount -> reject ---
    dec_bad_amount = await sp.handle_pre_checkout(r, order_id=order.order_id,
                                                  user_id=111, stars_amount=stars + 1)
    assert dec_bad_amount.ok is False
    print("pre-checkout rejected wrong amount")

    # --- pre-checkout: wrong user -> reject ---
    dec_bad_user = await sp.handle_pre_checkout(r, order_id=order.order_id,
                                                user_id=999, stars_amount=stars)
    assert dec_bad_user.ok is False
    print("pre-checkout rejected wrong user")

    # --- successful_payment: wrong amount -> not paid ---
    out_bad_amt = await sp.handle_successful_payment(
        r, order_id=order.order_id, user_id=111, charge_id="ch_1",
        currency="XTR", stars_amount=stars + 5,
    )
    assert out_bad_amt != "paid"
    order_after = await r.get_order_by_id(order.order_id)
    assert order_after.payment_status == "pending"
    print("successful_payment: wrong amount did not pay")

    # --- successful_payment: wrong currency -> not paid ---
    out_bad_cur = await sp.handle_successful_payment(
        r, order_id=order.order_id, user_id=111, charge_id="ch_2",
        currency="USD", stars_amount=stars,
    )
    assert out_bad_cur != "paid"
    print("successful_payment: wrong currency did not pay")

    # --- successful_payment: correct -> paid, charge stored, order->processing ---
    out_ok = await sp.handle_successful_payment(
        r, order_id=order.order_id, user_id=111, charge_id="ch_XTR_001",
        currency="XTR", stars_amount=stars,
    )
    assert out_ok == "paid", out_ok
    paid = await r.get_order_by_id(order.order_id)
    assert paid.payment_status == "paid"
    assert paid.telegram_payment_charge_id == "ch_XTR_001"
    assert paid.paid_at is not None
    assert paid.order_status == "processing"
    print("successful_payment: order paid + charge stored")

    # --- idempotency: same charge cannot pay another order ---
    order2 = await r.create_order(
        tg_user_id=222, username="buyer2", platform="tiktok", service=likes,
        quantity=1000, target_url="https://tiktok.com/@x/video/2",
    )
    stars2 = stars_svc.fiat_to_stars(Decimal(str(order2.total_price)))
    await r.set_stars_amount(order2.order_id, stars2)
    out_reuse = await sp.handle_successful_payment(
        r, order_id=order2.order_id, user_id=222, charge_id="ch_XTR_001",
        currency="XTR", stars_amount=stars2,
    )
    assert out_reuse != "paid"
    assert (await r.get_order_by_id(order2.order_id)).payment_status == "pending"
    print("idempotency: reused charge did not pay second order")

    # --- duplicate successful_payment on same order is a no-op ---
    out_dup = await sp.handle_successful_payment(
        r, order_id=order.order_id, user_id=111, charge_id="ch_XTR_001",
        currency="XTR", stars_amount=stars,
    )
    assert out_dup != "paid"  # already paid
    assert (await r.get_order_by_id(order.order_id)).payment_status == "paid"
    print("duplicate successful_payment is a no-op")

    # --- cancel a pending order ---
    order3 = await r.create_order(
        tg_user_id=333, username="buyer3", platform="youtube", service=likes,
        quantity=1000, target_url="https://youtube.com/@x/watch?v=1",
    )
    stars3 = stars_svc.fiat_to_stars(Decimal(str(order3.total_price)))
    await r.set_stars_amount(order3.order_id, stars3)
    cancelled = await r.cancel_crypto_order(order3.order_id)
    assert cancelled.payment_status == "cancelled"
    dec_cancel = await sp.handle_pre_checkout(r, order_id=order3.order_id,
                                              user_id=333, stars_amount=stars3)
    assert dec_cancel.ok is False  # cancelled order must be rejected
    print("cancelled order rejected at pre-checkout")

    await r.close()
    print("STARS TEST PASSED")


if __name__ == "__main__":
    asyncio.run(main())