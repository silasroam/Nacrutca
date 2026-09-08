"""
Crypto payment verification service (section 14 of the spec).

Implements the full server-side "Оплатил" flow:

    1.  resolve order from callback
    2.  ownership check (order belongs to the caller)
    3.  payment_status == pending
    4.  invoice not expired
    5.  call the network adapter (server-side data only)
    6.  currency match
    7.  exact amount match (Decimal)
    8.  transaction time sanity
    9.  transaction hash never been used before (idempotency)
    10. confirmations/finality where applicable
    11. only then -> payment_status = paid + transaction_hash stored

The verification NEVER trusts a user-supplied amount / price / tx hash.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ..database.repository import Repository
from . import crypto_rates as rates
from .blockchain import get_checker

logger = logging.getLogger(__name__)


def _ensure_aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; normalize to UTC-aware."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class CryptoInvoiceError(Exception):
    """User-facing error while verifying a crypto payment."""


async def create_crypto_invoice(repo: Repository, *, order,
                                currency: str) -> tuple[str, str]:
    """Create a fresh crypto invoice for an order.

    Returns (crypto_amount_str, wallet_address). The rate is fetched live and
    frozen on the order. Raises CryptoInvoiceError with a friendly message if
    the rate can't be fetched.

    The invoice deadline is the order's own `expires_at` (set at order creation,
    created_at + 30 min). Choosing a currency never pushes the deadline.
    """
    code = currency.upper()
    if code not in rates.CRYPTO_WALLETS:
        raise CryptoInvoiceError(f"Неизвестная криптовалюта: {currency}")

    try:
        rate = await rates.get_rate(code)
    except Exception:  # noqa: BLE001 - don't leak internals
        rate = None
    if rate is None or rate <= Decimal(0):
        raise CryptoInvoiceError(
            "Не удалось получить актуальный курс. Попробуйте позже или выберите другую валюту."
        )

    fiat = Decimal(str(order.total_price))
    crypto_amount = rates.crypto_amount_for_fiat(fiat, rate)
    wallet = rates.CRYPTO_WALLETS[code]

    await repo.save_crypto_invoice(
        order_id=order.order_id,
        currency=code,
        crypto_amount=rates.format_decimal(crypto_amount),
        exchange_rate=rates.format_decimal(rate),
        wallet_address=wallet,
        # expires_at defaults to the order's already-set deadline.
    )
    return rates.format_decimal(crypto_amount), wallet


async def verify_crypto_payment(repo: Repository, *, order,
                                user_id: int) -> "VerifyOutcome":
    """Run the server-side verification. Returns a typed outcome."""
    # 1/2/3. existence + ownership + pending
    if order is None:
        return VerifyOutcome(status="error", text="Заказ не найден.")
    if order.telegram_user_id != user_id:
        return VerifyOutcome(status="error", text="Это не ваш заказ.")
    if order.payment_status != "pending":
        return VerifyOutcome(
            status="already", text="Заказ уже обработан. Его нельзя проверить повторно."
        )
    # 4. not expired (now UTC >= expires_at UTC -> expired)
    logger.debug(
        "[PAYMENT DEBUG] order_id=%s user_id=%s created_at=%s expires_at=%s "
        "now=%s payment_status=%s",
        order.order_id, user_id, getattr(order, "created_at", None),
        getattr(order, "expires_at", None), datetime.now(timezone.utc),
        order.payment_status,
    )
    if order.expires_at is not None:
        exp = _ensure_aware(order.expires_at)
        if datetime.now(timezone.utc) >= exp:
            return VerifyOutcome(status="expired", text="Срок действия счёта истёк.")

    code = (order.crypto_currency or "").upper()
    if code not in ("TRX", "LTC", "TON", "SOL"):
        return VerifyOutcome(status="error", text="Нет данных о крипто-инвойсе.")

    try:
        expected = Decimal(order.crypto_amount or "0")
    except Exception:  # noqa: BLE001
        return VerifyOutcome(status="error", text="Повреждённые данные инвойса.")

    # 5. server-side lookup via the network adapter
    checker = get_checker(code)
    try:
        result = await checker.check(wallet_address=order.wallet_address,
                                     expected_amount=expected)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Crypto check error for order %s: %s", order.order_id, exc)
        return VerifyOutcome(
            status="retry", text="Ошибка сети при проверке. Попробуйте ещё раз."
        )

    if not result.found:
        return VerifyOutcome(
            status="notfound",
            text="⏳ Платёж пока не найден.\n\nМы не обнаружили перевод по вашему заказу.",
        )

    # 6. currency (adapters already scoped, but double-check)
    if result.currency != code:
        return VerifyOutcome(status="error", text="Валюта транзакции не совпадает.")
    # 7. exact amount
    if result.amount != expected:
        return VerifyOutcome(status="underpaid", text=_underpaid_message(expected, result.amount))
    # 8. timestamp sanity
    now = datetime.now(timezone.utc)
    if result.timestamp is not None and result.timestamp > now + timedelta(minutes=15):
        return VerifyOutcome(status="error", text="Транзакция датирована из будущего.")
    # 9. idempotency - never reuse a tx hash
    if not result.transaction_hash:
        return VerifyOutcome(status="retry", text="Не удалось получить хэш транзакции.")
    if await repo.is_transaction_used(result.transaction_hash):
        return VerifyOutcome(
            status="already", text="Эта транзакция уже использована для другого заказа."
        )
    # 11. confirm/finality
    if not result.is_final:
        return VerifyOutcome(
            status="notfound",
            text="Транзакция найдена, но сеть ещё не подтвердила её. Подождите и попробуйте снова.",
        )

    # 12. commit (only now)
    confirmed = await repo.confirm_crypto_payment(
        order.order_id, result.transaction_hash, rates.format_decimal(result.amount)
    )
    if confirmed is None:
        return VerifyOutcome(status="error", text="Не удалось обновить заказ.")
    if confirmed.payment_status != "paid":
        return VerifyOutcome(status="already", text="Заказ уже обработан ранее.")
    logger.info("Order %s paid via %s tx %s", order.order_id, code, result.transaction_hash)
    return VerifyOutcome(
        status="paid",
        text=(
            "✅ Оплата подтверждена\n\n"
            f"Заказ: <b>#{order.order_id}</b>\n"
            f"Сумма: {rates.format_decimal(result.amount)} {code}\n\n"
            "Оплата успешно получена.\n\n"
            "🟡 Заказ передан в обработку."
        ),
    )


def _underpaid_message(expected: Decimal, received: Decimal) -> str:
    return (
        "⚠️ Получена неверная сумма\n\n"
        f"Ожидалось: <b>{rates.format_decimal(expected)}</b>\n"
        f"Получено: <b>{rates.format_decimal(received)}</b>\n\n"
        "Статус заказа не изменён."
    )


class VerifyOutcome:
    """Typed result of :func:`verify_crypto_payment`."""

    __slots__ = ("status", "text")

    def __init__(self, status: str, text: str) -> None:
        self.status = status  # paid|notfound|underpaid|already|expired|retry|error
        self.text = text