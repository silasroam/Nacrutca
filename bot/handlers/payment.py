"""
Payment flow (section 11, 21): selection of crypto/stars -> order is persisted
with a server-computed price -> provider starts the payment -> confirmation
arrives only through provider/webhook callbacks (marked paid by the idempotent
repository.confirm_payment, never by a button press).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram import LabeledPrice, Update
from telegram.ext import CallbackContext, CallbackQueryHandler, PreCheckoutQueryHandler
from telegram.ext import MessageHandler, filters

from ..keyboards import constants as C
from ..keyboards import orders as kb_orders
from ..keyboards import payments as kb_payments
from ..services import crypto_rates as rates
from ..services.crypto_payment import (
    CryptoInvoiceError,
    create_crypto_invoice,
    verify_crypto_payment,
)
from ..services.stars_payment import (
    handle_pre_checkout as stars_pre_checkout,
    handle_successful_payment as stars_successful,
    order_id_from_payload as stars_order_from_payload,
    payload_for_order as stars_payload,
)
from ..services.orders import fmt_price
from ..states.order import OrderState
from ..database.models import Order, Service
from .common import (
    get_repo,
    set_state,
    reset_flow,
    ensure_draft,
    answer_query,
    safe_answer,
)

logger = logging.getLogger(__name__)


async def pay_method_selected(method: str, update: Update, context: CallbackContext,
                              service_id: int) -> None:
    query = update.callback_query
    await answer_query(query)
    user = update.effective_user

    repo = get_repo(context)
    draft = ensure_draft(context)
    svc: Service | None = draft.get("service")
    quantity: int | None = draft.get("quantity")
    platform: str | None = draft.get("platform")
    target_url: str | None = draft.get("target_url")

    if svc is None or not quantity or not platform:
        reset_flow(context)
        await safe_answer(
            context, update.effective_chat.id,
            "❌ Заказ устарел. Начните заново — /start",
        )
        return

    # Server-side authoritative price (section 23): client never supplies it.
    order = await repo.create_order(
        tg_user_id=user.id,
        username=user.username or "",
        platform=platform,
        service=svc,
        quantity=quantity,
        target_url=target_url or "",
    )
    await repo.set_payment_fields(order.order_id, method, "")

    # Remember the freshly-created order in the per-user state BEFORE branching,
    # so both the crypto currency handler and the stars handler can resolve the
    # current pending order (bugfix: crypto branch used to `return` before this,
    # leaving `pending_order_id` unset -> "Заказ устарел" for every currency).
    draft = ensure_draft(context)
    draft["order_id"] = order.order_id
    context.user_data["pending_order_id"] = order.order_id

    # The authoritative amount is the one recomputed & stored by the DB.
    total = order.total_price

    set_state(context, OrderState.PAYMENT_PROCESSING)

    if method == "crypto":
        # New flow (section 1): first pick the crypto currency.
        await safe_answer(
            context, update.effective_chat.id,
            "₿ <b>Оплата криптовалютой</b>\n\nВыберите валюту для оплаты:",
            reply_markup=kb_payments.crypto_wallets(order.order_id),
        )
        return
    elif method == "stars":
        # Step 1 (Stars): compute whole Stars total, save it, then show the
        # "before-pay" screen. The real invoice is sent only on "⭐ Оплатить".
        from decimal import Decimal
        from ..services import stars as stars_svc
        stars_total = stars_svc.fiat_to_stars(Decimal(str(total)))
        await repo.set_stars_amount(order.order_id, stars_total)
        await send_stars_invoice(update, context, order, stars_total)
        return


async def send_stars_invoice(update: Update, context: CallbackContext,
                             order: Order, stars_total: int) -> None:
    """Screen before paying with Stars: order, fiat, Stars total, course."""
    chat_id = update.effective_chat.id
    await safe_answer(
        context, chat_id,
        "⭐ <b>Оплата звёздами</b>\n\n"
        f"Заказ: <b>#{order.order_id}</b>\n"
        f"💰 К оплате: <b>{fmt_price(order.total_price)} ₽</b>\n"
        f"⭐ Стоимость: <b>{stars_total} ⭐</b>\n\n"
        "Курс:\n130 ₽ = 100 ⭐\n\n"
        "Нажмите кнопку ниже для оплаты.",
        reply_markup=kb_payments.stars_pay(order.order_id),
    )


async def crypto_payment(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    parts = query.data.split(C.SEP)   # cb:pay:crypto:<service_id>
    sid = parts[-1] if parts else ""
    await pay_method_selected("crypto", update, context, int(sid or 0))


# ---------------------------------------------------------------------------
# New crypto flow: currency -> invoice -> verify -> paid/cancel
# ---------------------------------------------------------------------------
async def crypto_wallet_selected(update: Update, context: CallbackContext) -> None:
    """User picked a currency (TRX/LTC/TON/SOL). Create the invoice.

    The order_id travels in the callback data (cb:crypto:w:<CODE>:<order_id>),
    so we always re-read the latest order from the DB (never trust a draft or a
    previously cached object) and validate ownership + pending + expiry.
    """
    query = update.callback_query
    await answer_query(query)
    parts = query.data.split(C.SEP)           # cb:crypto:w:<code>:<order_id>
    if len(parts) < 4:
        return
    code = parts[3].upper()
    user = update.effective_user
    order_id = _order_id_from_parts(query.data)

    repo = get_repo(context)
    order = await repo.get_order_by_id(order_id) if order_id else None

    # --- temporary diagnostics before the expiry check (spec request) ---
    logger.debug(
        "[PAYMENT DEBUG] order_id=%s user_id=%s created_at=%s expires_at=%s "
        "now=%s payment_status=%s",
        order.order_id if order else order_id,
        user.id,
        getattr(order, "created_at", None),
        getattr(order, "expires_at", None),
        datetime.now(timezone.utc),
        getattr(order, "payment_status", None),
    )

    if order is None:
        await _stale_order(update, context)
        return
    if order.telegram_user_id != user.id:
        await safe_answer(context, update.effective_chat.id, "Это не ваш заказ.")
        return
    if order.payment_status != "pending":
        await safe_answer(
            context, update.effective_chat.id,
            "Заказ уже обработан. Начните новый — /start",
        )
        return
    if _order_expired(order):
        await _stale_order(update, context)
        return

    try:
        crypto_amount, wallet = await create_crypto_invoice(repo, order=order, currency=code)
    except CryptoInvoiceError as exc:
        await safe_answer(context, update.effective_chat.id, f"❌ {exc}")
        return
    except Exception:  # noqa: BLE001
        logger.exception("Crypto invoice creation failed")
        await safe_answer(
            context, update.effective_chat.id,
            "❌ Не удалось создать счёт. Попробуйте ещё раз.",
        )
        return

    rate = order.exchange_rate or "?"
    ttl_min = _invoice_ttl_minutes()
    name = rates.CRYPTO_NAMES.get(code, code)
    emoji = rates.CRYPTO_EMOJIS.get(code, "")

    text = (
        "💳 Оплата криптовалютой\n\n"
        f"Заказ: <b>#{order.order_id}</b>\n"
        f"💰 К оплате: <b>{fmt_price(order.total_price)} ₽</b>\n"
        f"{emoji} Валюта: <b>{name}</b>\n\n"
        f"Курс:\n1 {code} = {rate} ₽\n\n"
        f"Сумма к оплате:\n\n<code>{crypto_amount} {code}</code>\n\n"
        f"Адрес для оплаты:\n<code>{wallet}</code>\n\n"
        "Отправьте указанную сумму на этот адрес.\n\n"
        f"⏱ Счёт действителен {ttl_min} минут."
    )
    context.user_data["pending_order_id"] = order.order_id
    set_state(context, OrderState.PAYMENT_PROCESSING)
    await safe_answer(
        context, update.effective_chat.id, text,
        reply_markup=kb_payments.crypto_invoice(order.order_id),
    )


async def crypto_paid(update: Update, context: CallbackContext) -> None:
    """User pressed '✅ Оплатил' -> start server-side blockchain verification."""
    query = update.callback_query
    await answer_query(query)
    order_id = _order_id_from_parts(query.data)
    user = update.effective_user
    if order_id is None:
        return
    if not _rate_limited(context, "crypto_check_ts", 10):
        await safe_answer(
            context, update.effective_chat.id,
            "⏳ Пожалуйста, подождите. Проверка выполняется не чаще раза в 10 секунд.",
        )
        return
    repo = get_repo(context)
    order = await repo.get_order_by_id(order_id)
    outcome = await verify_crypto_payment(repo, order=order, user_id=user.id)
    await _render_outcome(update, context, order_id, outcome)


async def crypto_recheck(update: Update, context: CallbackContext) -> None:
    """User pressed '🔄 Проверить снова'."""
    query = update.callback_query
    await answer_query(query)
    order_id = _order_id_from_parts(query.data)
    user = update.effective_user
    if order_id is None:
        return
    if not _rate_limited(context, "crypto_check_ts", 10):
        await safe_answer(
            context, update.effective_chat.id,
            "⏳ Проверка уже выполняется. Подождите немного.",
        )
        return
    repo = get_repo(context)
    order = await repo.get_order_by_id(order_id)
    outcome = await verify_crypto_payment(repo, order=order, user_id=user.id)
    await _render_outcome(update, context, order_id, outcome)


async def crypto_cancel(update: Update, context: CallbackContext) -> None:
    """User pressed '❌ Отменить'. Cancel the pending order."""
    query = update.callback_query
    await answer_query(query)
    order_id = _order_id_from_parts(query.data)
    user = update.effective_user
    if order_id is None:
        return
    repo = get_repo(context)
    order = await repo.get_order_by_id(order_id)
    if order is None or order.telegram_user_id != user.id:
        await safe_answer(context, update.effective_chat.id, "Заказ не найден.")
        return
    updated = await repo.cancel_crypto_order(order_id)
    markup = None
    if updated and updated.payment_status == "cancelled":
        text = f"❌ Оплата отменена\n\nЗаказ #{order_id} отменён."
    else:
        st = updated.payment_status if updated else "?"
        text = (
            f"Заказ #{order_id}: его уже нельзя отменить "
            f"(текущий статус: {st})."
        )
        markup = kb_orders.order_actions(order_id)
    reset_flow(context)
    await safe_answer(context, update.effective_chat.id, text, reply_markup=markup)


async def _render_outcome(update, context, order_id: int, outcome) -> None:
    """Render a VerifyOutcome as a message + follow-up keyboard."""
    chat_id = update.effective_chat.id
    if outcome.status == "paid":
        await safe_answer(context, chat_id, outcome.text)
        return
    if outcome.status == "notfound":
        await safe_answer(
            context, chat_id, outcome.text,
            reply_markup=kb_payments.crypto_not_found(order_id),
        )
        return
    if outcome.status in ("underpaid", "already", "expired", "error", "retry"):
        await safe_answer(context, chat_id, outcome.text)
        return


def _ensure_aware_utc(value: datetime) -> datetime:
    """Normalize a possibly-naive datetime (SQLite) to UTC-aware."""
    if value is None:
        return None  # type: ignore[return-value]
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _order_expired(order) -> bool:
    """True only when `now >= expires_at`. expires_at is compared in UTC."""
    if order.expires_at is None:
        return False
    now = datetime.now(timezone.utc)
    return now >= _ensure_aware_utc(order.expires_at)


async def _stale_order(update, context) -> None:
    reset_flow(context)
    await safe_answer(
        context, update.effective_chat.id,
        "❌ Заказ устарел. Начните заново — /start",
    )


async def _pending_draft_order(context, repo, user_id):
    """Return the order being currently processed from draft or pending_order_id."""
    order_id = context.user_data.get("pending_order_id")
    if order_id:
        order = await repo.get_order_by_id(int(order_id))
        if order and order.telegram_user_id == user_id:
            return order
    draft = context.user_data.get("draft") or {}
    order_id = draft.get("order_id")
    if order_id:
        return await repo.get_order_by_id(int(order_id))
    return None


def _order_id_from_parts(data: str) -> int | None:
    parts = data.split(C.SEP)
    for token in reversed(parts):
        if token.isdigit():
            return int(token)
    return None


def _rate_limited(context, key: str, min_seconds: int) -> bool:
    """Simple per-user cooldown to avoid spamming the blockchain API."""
    import time
    last = context.user_data.get(key, 0)
    now = time.time()
    if now - last < min_seconds:
        return False
    context.user_data[key] = now
    return True


def _invoice_ttl_minutes() -> int:
    from ..config import get_settings
    return max(1, get_settings().crypto_invoice_ttl_seconds // 60)


async def stars_payment(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    parts = query.data.split(C.SEP)
    sid = parts[-1] if parts else ""
    await pay_method_selected("stars", update, context, int(sid or 0))


# ---------------------------------------------------------------------------
# Native Payments: pre-checkout / successful payment (Stars)
# ---------------------------------------------------------------------------
async def pre_checkout(update: Update, context: CallbackContext) -> None:
    q = update.pre_checkout_query
    order_id = stars_order_from_payload(q.invoice_payload)
    if order_id is None:
        await q.answer(ok=False, error_message="Некорректный инвойс.")
        return
    stars_amount = q.total_amount  # whole XTR amount from the invoice
    decision = await stars_pre_checkout(
        get_repo(context), order_id=order_id,
        user_id=q.from_user.id, stars_amount=int(stars_amount),
    )
    await q.answer(ok=decision.ok, error_message=None if decision.ok else decision.message)


async def successful_payment(update: Update, context: CallbackContext) -> None:
    """The real Telegram `successful_payment` — the ONLY thing that pays Stars."""
    sp = update.effective_message.successful_payment
    order_id = stars_order_from_payload(sp.invoice_payload)
    if order_id is None:
        logger.warning("Stars successful_payment with unparseable payload")
        return
    outcome = await stars_successful(
        get_repo(context),
        order_id=order_id,
        user_id=sp.from_user.id if sp.from_user else 0,
        charge_id=sp.telegram_payment_charge_id or "",
        currency=sp.currency,
        stars_amount=int(sp.total_amount or 0),
    )
    if outcome == "paid":
        await notify_paid(context, update.effective_chat.id, order_id)


async def notify_paid(context: CallbackContext, chat_id: int, order_id: int) -> None:
    repo = get_repo(context)
    order = await repo.get_order_by_id(order_id)
    if order is None:
        return
    emoji = "🟢 Выполнен" if order.order_status == "completed" else "🟡 Обрабатывается"
    await safe_answer(
        context, chat_id,
        f"✅ <b>Оплата подтверждена</b>\n\n"
        f"Заказ <b>#{order.order_id}</b> передан в обработку.\n"
        f"Статус: {emoji}",
        reply_markup=kb_orders.order_actions(order.order_id),
    )


async def stars_pay_click(update: Update, context: CallbackContext) -> None:
    """User pressed '⭐ Оплатить' -> send the official Telegram Stars invoice."""
    query = update.callback_query
    await answer_query(query)
    order_id = _order_id_from_parts(query.data)
    user = update.effective_user
    if order_id is None:
        return
    repo = get_repo(context)
    order = await repo.get_order_by_id(order_id)
    if order is None or order.telegram_user_id != user.id:
        await safe_answer(context, update.effective_chat.id, "Заказ не найден.")
        return
    if order.payment_status != "pending":
        await safe_answer(context, update.effective_chat.id, "Заказ уже обработан.")
        return
    if _order_expired(order):
        await _stale_order(update, context)
        return
    stars_amount = int(order.stars_amount or 0)
    if stars_amount <= 0:
        await safe_answer(context, update.effective_chat.id, "Ошибка: не указана сумма ⭐.")
        return
    payload = stars_payload(order.order_id)
    try:
        await context.bot.send_invoice(
            chat_id=user.id,
            title=f"Заказ #{order.order_id}",
            description=f"Оплата заказа #{order.order_id} звёздами.",
            payload=payload,
            provider_token="",       # Telegram Stars -> empty provider token
            currency="XTR",          # official Telegram Stars currency
            prices=[
                LabeledPrice(
                    label=f"Заказ #{order.order_id}",
                    amount=stars_amount,
                )
            ],
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send Stars invoice")
        await safe_answer(
            context, update.effective_chat.id,
            "❌ Не удалось создать инвойс. Попробуйте ещё раз.",
        )


async def stars_cancel_click(update: Update, context: CallbackContext) -> None:
    """User pressed '❌ Отменить' on the Stars before-pay screen."""
    query = update.callback_query
    await answer_query(query)
    order_id = _order_id_from_parts(query.data)
    user = update.effective_user
    if order_id is None:
        return
    repo = get_repo(context)
    order = await repo.get_order_by_id(order_id)
    if order is None or order.telegram_user_id != user.id:
        await safe_answer(context, update.effective_chat.id, "Заказ не найден.")
        return
    if order.payment_status == "pending":
        await repo.cancel_crypto_order(order_id)
        await safe_answer(
            context, update.effective_chat.id,
            f"❌ Оплата отменена\n\nЗаказ #{order_id} отменён.",
        )
    else:
        await safe_answer(
            context, update.effective_chat.id,
            f"Заказ #{order_id} уже обработан. Его нельзя отменить.",
        )
    reset_flow(context)


def register(app) -> None:
    app.add_handler(
        CallbackQueryHandler(crypto_payment, pattern="^" + C.CB_PAY_CRYPTO + ":")
    )
    app.add_handler(
        CallbackQueryHandler(stars_payment, pattern="^" + C.CB_PAY_STARS + ":")
    )
    app.add_handler(
        CallbackQueryHandler(stars_pay_click, pattern="^" + C.CB_STARS_PAY + ":")
    )
    app.add_handler(
        CallbackQueryHandler(stars_cancel_click, pattern="^" + C.CB_STARS_CANCEL + ":")
    )
    app.add_handler(
        CallbackQueryHandler(crypto_wallet_selected, pattern="^" + C.CB_CRYPTO_WALLET + ":")
    )
    app.add_handler(
        CallbackQueryHandler(crypto_paid, pattern="^" + C.CB_CRYPTO_PAID + ":")
    )
    app.add_handler(
        CallbackQueryHandler(crypto_recheck, pattern="^" + C.CB_CRYPTO_RECHECK + ":")
    )
    app.add_handler(
        CallbackQueryHandler(crypto_cancel, pattern="^" + C.CB_CRYPTO_CANCEL + ":")
    )
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(
        MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment, block=False)
    )