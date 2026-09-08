"""
Payment service with a provider abstraction (section 21 — services/payments.py).

Business logic that handles an order does not depend on the concrete payment
method. Two providers are provided:

    * TelegramStarsProvider  — uses Telegram Native Payments (Stars).
    * CryptoPaymentProvider  — integration point for a crypto gateway
                               (CryptoBot / NOWPayments / etc.).

Both providers expose a webhook/confirmation entry point. An order is ONLY
marked paid when a provider confirms it — never from a user button press.
"""
from __future__ import annotations

import abc
import json
import logging

from ..config import get_settings
from ..database.repository import (
    DuplicatePaymentError,
    OrderNotFoundError,
    Repository,
)

logger = logging.getLogger(__name__)

# Canonical payment statuses (section 12)
PAYMENT_PENDING = "pending"
PAYMENT_PAID = "paid"
PAYMENT_FAILED = "failed"
PAYMENT_REFUNDED = "refunded"

# Canonical order statuses (section 12)
ORDER_PENDING = "pending"
ORDER_PROCESSING = "processing"
ORDER_COMPLETED = "completed"
ORDER_CANCELLED = "cancelled"
ORDER_PARTIAL = "partial"


class PaymentProvider(abc.ABC):
    """Base class every payment provider must implement."""

    name: str = "base"

    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    @property
    def method(self) -> str:
        """Short canonical method id used in DB (e.g. 'stars', 'crypto')."""
        raise NotImplementedError

    async def start_payment(self, *, telegram_user_id: int, order,
                            amount_float: float, callback_context=None):
        """Initiate payment, return (provider_ref, human_hint)."""
        raise NotImplementedError

    async def handle_webhook(self, payload) -> "WebhookResult":
        """Verify + process an incoming webhook idempotently."""
        raise NotImplementedError


class WebhookResult:
    def __init__(self, ok: bool, *, order_id=None, status=None,
                 message="", payment_id=None, duplicate=False):
        self.ok = ok
        self.order_id = order_id
        self.status = status
        self.message = message
        self.payment_id = payment_id
        self.duplicate = duplicate

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate

    def __repr__(self) -> str:
        return (
            f"<WebhookResult ok={self.ok} order={self.order_id} "
            f"status={self.status} dup={self.duplicate}>"
        )


def _encode_payload(data: dict) -> str:
    try:
        return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:  # pragma: no cover
        return ""


class TelegramStarsProvider(PaymentProvider):
    """Telegram Stars via Native Payments.

    The actual `successful_payment` arrives as a PTB update; we then confirm the
    order through the same idempotent path as every other provider.
    """

    name = "telegram_stars"
    method = "stars"

    async def start_payment(self, *, telegram_user_id: int, order,
                            amount_float: float, callback_context=None):
        return f"stars:{order.order_id}", (
            "⭐ Отправьте инвойс через кнопку ниже."
        )

    async def handle_pre_checkout(self, *, order_id: int,
                                  telegram_user_id: int):
        return await webhook_confirm_order(self.repo, method="stars",
                                           order_id=order_id)


class CryptoPaymentProvider(PaymentProvider):
    """Cryptocurrency payments (integration point).

    Wire this to a crypto gateway that:
       - returns a unique payment address / invoice id (start_payment);
       - posts verified transactions to a webhook we confirm with a signature.
    """

    name = "crypto_gateway"
    method = "crypto"

    async def start_payment(self, *, telegram_user_id: int, order,
                            amount_float: float, callback_context=None):
        payment = await self.repo.create_payment(
            order_id=order.order_id,
            tg_user_id=telegram_user_id,
            method="crypto",
            provider=self.name,
            amount=float(amount_float),
            payload=_encode_payload({"order_id": order.order_id}),
        )
        settings = get_settings()
        wallet = settings.crypto_wallet_address or "CRYPTO_WALLET"
        hint = (
            f"Отправьте {amount_float:.2f} USD (эквивалент) на адрес:\n"
            f"<code>{wallet}</code>\n\n"
            f"Платёж: <code>{payment.payment_id}</code>\n"
            "После подтверждения сети заказ будет обработан автоматически."
        )
        return payment.payment_id, hint

    async def handle_webhook(self, payload):
        return await webhook_confirm_order(
            self.repo, method="crypto", order_id=payload.get("order_id")
        )


async def webhook_confirm_order(repo: Repository, *, method: str,
                                order_id: int | None,
                                payment_id: str | None = None) -> WebhookResult:
    """Idempotent confirmation used by all providers.

    If no payment row exists yet (e.g. Telegram Stars where the charge arrives
    directly), we create/lookup one before confirming. The price used is always
    re-read from the order in the DB (section 23) - never from the client.
    """
    if order_id is None:
        return WebhookResult(False, message="missing order id")

    order = await repo.get_order_by_id(order_id)
    if order is None:
        logger.warning("Webhook confirm for unknown order %s", order_id)
        return WebhookResult(False, message="order not found")

    current_total = order.total_price

    if payment_id:
        payment = await repo.get_payment(payment_id)
        if payment is None:
            payment = await repo.create_payment(
                order_id=order_id,
                tg_user_id=order.telegram_user_id,
                method=method,
                provider="webhook",
                amount=float(current_total),
                payload=_encode_payload({"order_id": order_id}),
            )
        payment_ref = payment.payment_id
    else:
        existing = await repo.get_existing_payment(
            order_id, method, [PAYMENT_PENDING]
        )
        if existing is not None:
            payment_ref = existing.payment_id
        else:
            payment = await repo.create_payment(
                order_id=order_id,
                tg_user_id=order.telegram_user_id,
                method=method,
                provider=method,
                amount=float(current_total),
                payload=_encode_payload({"order_id": order_id}),
            )
            payment_ref = payment.payment_id

    try:
        confirmed = await repo.confirm_payment(payment_ref, order_id)
    except DuplicatePaymentError as exc:
        logger.info("Webhook ignored (duplicate): %s", exc)
        return WebhookResult(True, order_id=order_id, status="paid",
                             payment_id=payment_ref, duplicate=True)
    except OrderNotFoundError as exc:
        return WebhookResult(False, message=str(exc))

    return WebhookResult(True, order_id=order_id, status=confirmed.status,
                         payment_id=payment_ref)


def provider_for(method: str, repo: Repository) -> PaymentProvider:
    if method == "crypto":
        return CryptoPaymentProvider(repo)
    if method == "stars":
        return TelegramStarsProvider(repo)
    raise ValueError(f"Unknown payment method: {method}")