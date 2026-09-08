"""
Telegram Stars pay resolution (section: Telegram Stars payment flow).

These helpers are used by the payment handlers for the *actual* Telegram
Payments flow (currency XTR). They never trust the client blindly - every
decision re-checks the order from the DB and the amount/currency reported by
Telegram.

Two idempotent stages:
  1. handle_pre_checkout  -> decide answer(ok=True/False)
  2. handle_successful    -> mark order paid once (charge id is unique)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from ..database.repository import Repository
from . import stars as stars_svc

logger = logging.getLogger(__name__)


class PreCheckoutDecision:
    __slots__ = ("ok", "message")

    def __init__(self, ok: bool, message: str = ""):
        self.ok = ok
        self.message = message

    def __bool__(self) -> bool:
        return self.ok


# ---------------------------------------------------------------------------
# Shared validations
# ---------------------------------------------------------------------------
def _ensure_aware(value) -> datetime:
    if value is None:
        return None  # type: ignore[return-value]
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _order_expired(order) -> bool:
    if order.expires_at is None:
        return False
    return datetime.now(timezone.utc) >= _ensure_aware(order.expires_at)


def validate_vendor_order(order, user_id: int):
    """Return (ok, message). Validates an order before Stars payment."""
    if order is None:
        return False, "Заказ не найден."
    if order.telegram_user_id != user_id:
        return False, "Это не ваш заказ."
    if order.payment_status == "cancelled":
        return False, "Заказ отменён."
    if order.payment_status == "paid":
        return False, "Заказ уже оплачен."
    if order.payment_status != "pending":
        return False, "Заказ нельзя оплатить в текущем статусе."
    if _order_expired(order):
        return False, "Срок действия заказа истёк."
    return True, ""


def amount_matches(order, stars_amount: int) -> bool:
    """The invoice Stars amount must equal the stored server-computed value."""
    return int(order.stars_amount or 0) == int(stars_amount)


# ---------------------------------------------------------------------------
# Pre-checkout
# ---------------------------------------------------------------------------
async def handle_pre_checkout(repo: Repository, *, order_id: int,
                              user_id: int, stars_amount: int) -> PreCheckoutDecision:
    """Called from the Telegram pre-checkout handler."""
    order = await repo.get_order_by_id(order_id) if order_id else None
    ok, msg = validate_vendor_order(order, user_id)
    if not ok:
        return PreCheckoutDecision(False, msg)
    if not amount_matches(order, stars_amount):
        return PreCheckoutDecision(
            False,
            "Сумма инвойса не соответствует заказу. Оформите заказ заново.",
        )
    # All good -> Telegram can proceed with the official Stars charge.
    return PreCheckoutDecision(True, "")


# ---------------------------------------------------------------------------
# Successful payment (the ONLY thing that marks a Stars order as paid)
# ---------------------------------------------------------------------------
async def handle_successful_payment(repo: Repository, *, order_id: int,
                                    user_id: int, charge_id: str,
                                    currency: str, stars_amount: int) -> str:
    """Mark an order paid after a real Telegram Stars successful_payment.

    Returns a single-line outcome for logging. Performs the full chain:
    ownership, pending/cancelled/paid state, expiry, currency == XTR,
    amount == stars_amount, idempotency on charge_id. Never confirmed blindly.
    """
    order = await repo.get_order_by_id(order_id) if order_id else None
    ok, msg = validate_vendor_order(order, user_id)
    if not ok:
        logger.warning(
            "Stars successful ignored for order %s (user %s): %s",
            order_id, user_id, msg,
        )
        return msg

    if currency != "XTR":
        logger.warning(
            "Stars successful for order %s has wrong currency %r", order_id, currency
        )
        return "Неверная валюта платежа."
    if not amount_matches(order, stars_amount):
        logger.warning(
            "Stars amount mismatch for order %s: invoiced=%s paid=%s",
            order_id, order.stars_amount, stars_amount,
        )
        return "Сумма платежа не совпадает с заказом."

    if charge_id and await repo.is_charge_used(charge_id):
        logger.info("Stars charge %s already used elsewhere; order %s not re-paid",
                    charge_id, order_id)
        return "Платёж уже использован."

    confirmed = await repo.confirm_stars_payment(order_id, charge_id or "")
    if confirmed is None:
        return "Заказ не найден при подтверждении."
    if confirmed.payment_status != "paid":
        return "Заказ уже оплачен ранее."
    logger.info(
        "Stars order %s paid (charge=%s, amount=%s)",
        order_id, charge_id, stars_amount,
    )
    return "paid"


def payload_for_order(order_id: int) -> str:
    """Unique invoice payload bound to a single order."""
    import secrets
    return f"order:{order_id}:{secrets.token_hex(4)}"


def order_id_from_payload(payload: str | None) -> int | None:
    if not payload:
        return None
    try:
        return int(payload.split(":")[1])
    except (IndexError, ValueError):
        return None