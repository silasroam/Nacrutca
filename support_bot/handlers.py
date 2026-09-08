"""Handlers for the Support Bot.

Security model (every operation re-checks server-side):
  * ticket ownership: a user may only open their own ticket; callback payloads
    carry only a ticket_id and are re-validated against the DB.
  * admin auth: uses ADMIN_TELEGRAM_ID (user numeric id), never username.
  * no secrets ever reach messages.

Flows:
  USER  : /start -> main menu -> "Создать обращение" -> text/photo -> ticket
        : "Мои обращения" -> pick own ticket -> conversation history
        : plain text while an open ticket exists -> routed as a reply
  ADMIN : Support Panel -> active tickets -> open -> "Ответить"/"Закрыть"
        : replies typed by admin are delivered to the ticket owner.
"""
from __future__ import annotations

import logging
import re

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import keyboards as kb
from .config import get_settings
from .db import SupportRepository
from .models import (
    CONTENT_DOCUMENT,
    CONTENT_PHOTO,
    CONTENT_TEXT,
    CONTENT_VIDEO,
    STATUS_CLOSED,
    STATUS_WAITING_ADMIN,
    STATUS_WAITING_USER,
)
from .states import SupportState

logger = logging.getLogger(__name__)

# state key inside context.user_data (namespaced, no clash with Traffic Bot)
STATE = "sup_state"
ADMIN_REPLY_TICKET = "sup_admin_reply"
PENDING_ORDER = "sup_pending_order"

# Matches "#1842" / "1842" inside user text.
_ORDER_REF_RE = re.compile(r"#?\b(\d{3,8})\b")

REPO_KEY = "support_repo"

MAIN_MENU_TEXT = (
    "🛟 <b>SUPPORT</b>\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "Служба поддержки Traffic Bot\n\n"
    "Здесь можно:\n"
    "◆ Создать обращение\n"
    "◆ Проверить существующее обращение\n"
    "◆ Получить ответ оператора\n\n"
    "📦 Если вопрос связан с заказом, укажите его номер.\n"
    "━━━━━━━━━━━━━━━━━━"
)


def get_repo(context: ContextTypes.DEFAULT_TYPE) -> SupportRepository:
    return context.bot_data[REPO_KEY]


def _set_state(context, state) -> None:
    context.user_data[STATE] = state


def _get_state(context, default=None):
    return context.user_data.get(STATE, default)


def _reset_state(context) -> None:
    context.user_data.pop(STATE, None)
    context.user_data.pop(ADMIN_REPLY_TICKET, None)


async def answer_query(query, text: str | None = None, alert: bool = False) -> None:
    try:
        await query.answer(text=text, show_alert=alert)
    except Exception as exc:  # pragma: no cover - network errors
        logger.warning("answer_query failed: %s", exc)


async def safe_send(context, chat_id: int, text: str,
                    reply_markup=None, parse_mode: str = "HTML") -> None:
    try:
        await context.bot.send_message(
            chat_id=chat_id, text=text, reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except Exception as exc:  # pragma: no cover - network errors
        logger.warning("safe_send failed: %s", exc)


def _user_display(ticket) -> str:
    """Human label for a ticket owner, never leaking IDs unless needed."""
    return f"@{ticket.username}" if ticket.username else f"id {ticket.user_id}"


async def start_command(update: Update, context: CallbackContext) -> None:
    """/start entry point. Optional deep link: /start ticket_1842."""
    user = update.effective_user
    if user is None:
        return
    _reset_state(context)
    await get_repo(context).upsert_user(
        user.id, user.username or "", user.first_name or ""
    )

    # Deep link support: /start ticket_<order_id> pre-links a new ticket.
    # context.args is the canonical PTB way to read deep-link arguments.
    pending_order = None
    args = context.args or []
    for arg in args:
        m = re.match(r"ticket_(\d+)$", arg)
        if m:
            oid = int(m.group(1))
            order = await get_repo(context).find_order_by_id(oid)
            if order is not None and order.telegram_user_id == user.id:
                pending_order = oid  # ownership verified server-side
            break
    context.user_data[PENDING_ORDER] = pending_order

    is_admin = get_settings().is_admin(user.id)
    await _show_main_menu(context, update.effective_chat.id, is_admin=is_admin)
    if pending_order:
        await safe_send(
            context, update.effective_chat.id,
            f"📦 Будет привязан заказ <code>#{pending_order}</code>.\n"
            "Нажмите «📝 Создать обращение», чтобы описать проблему.",
        )


async def _show_main_menu(context, chat_id: int, is_admin: bool = False) -> None:
    # The flag is passed explicitly by callers that know the user; the plain
    # text path defaults to a user menu, and admin-only actions always re-check
    # ADMIN_TELEGRAM_ID server-side anyway.
    await safe_send(context, chat_id, MAIN_MENU_TEXT,
                    reply_markup=kb.main_menu(is_admin=is_admin))


async def cancel_creation(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    _reset_state(context)
    context.user_data.pop(PENDING_ORDER, None)
    await safe_send(
        context, update.effective_chat.id,
        "❌ Создание обращения отменено.", reply_markup=None,
    )
    is_admin = bool(
        update.effective_user and get_settings().is_admin(update.effective_user.id)
    )
    await _show_main_menu(context, update.effective_chat.id, is_admin=is_admin)


async def main_menu_back(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    _reset_state(context)
    is_admin = bool(
        update.effective_user and get_settings().is_admin(update.effective_user.id)
    )
    await _show_main_menu(context, update.effective_chat.id, is_admin=is_admin)


async def new_ticket_menu(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    _set_state(context, SupportState.creating)
    order_hint = ""
    if context.user_data.get(PENDING_ORDER):
        order_hint = f"\n📦 Будет привязан заказ <code>#{context.user_data[PENDING_ORDER]}</code>\n"
    await safe_send(
        context, update.effective_chat.id,
        "📝 <b>НОВОЕ ОБРАЩЕНИЕ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Опишите вашу проблему одним сообщением.\n\n"
        "Если вопрос связан с заказом, укажите его ID:\n"
        "например <code>#1842</code>" + order_hint + "\n"
        "Можно отправить текст, фото или другой поддерживаемый Telegram-контент.\n"
        "━━━━━━━━━━━━━━━━━━",
        reply_markup=kb.creating(),
    )


# ---------------------------------------------------------------------------
# Creating a new ticket (text / photo / document / video)
# ---------------------------------------------------------------------------
async def receive_ticket_message(update: Update, context: CallbackContext) -> None:
    """Handle the user's message that describes a new ticket."""
    user = update.effective_user
    if user is None:
        return
    message = update.effective_message
    text = ""
    content_type = CONTENT_TEXT
    telegram_message_id = message.message_id if message else None
    if message.photo:
        content_type = CONTENT_PHOTO
        text = message.caption or ""
    elif message.document:
        content_type = CONTENT_DOCUMENT
        text = message.caption or ""
    elif message.video:
        content_type = CONTENT_VIDEO
        text = message.caption or ""
    else:
        text = (message.text or "").strip()

    if not text and content_type == CONTENT_TEXT:
        await safe_send(
            context, update.effective_chat.id,
            "⚠️ Пожалуйста, отправьте текстовое описание проблемы или фото/файл "
            "с текстом.", reply_markup=kb.creating(),
        )
        return

    repo = get_repo(context)
    # Auto-resolve an order if the user referenced #<id> and owns it.
    order_id = None
    m = _ORDER_REF_RE.search(text)
    if m:
        candidate = int(m.group(1))
        order = await repo.find_order_by_id(candidate)
        if order is not None and order.telegram_user_id == user.id:
            order_id = candidate
    # Deep-link pending order takes precedence if no #ref in text.
    if order_id is None and context.user_data.get(PENDING_ORDER):
        order_id = context.user_data[PENDING_ORDER]

    ticket = await repo.create_ticket(
        user_id=user.id,
        username=user.username or "",
        order_id=order_id,
        text=text,
        content_type=content_type,
        telegram_message_id=telegram_message_id,
    )
    _reset_state(context)
    context.user_data.pop(PENDING_ORDER, None)

    await safe_send(
        context, update.effective_chat.id,
        "✅ Обращение создано!\n\n"
        f"🆔 Ваше обращение: <code>#{ticket.ticket_id}</code>\n"
        "Оператор ответит в ближайшее время. Ответ придёт сюда автоматически.",
        reply_markup=kb.main_menu(is_admin=get_settings().is_admin(user.id)),
    )
    await _notify_admin_new_ticket(context, update, ticket, text, content_type)


def _format_history(ticket, messages) -> str:
    parts = [
        "🗒 <b>История переписки</b>\n",
        f"#{ticket.ticket_id} · {kb.status_label(ticket.status)}\n",
        "━━━━━━━━━━━━━━━━━━",
    ]
    for m in messages:
        who = "👤 Пользователь" if m.sender_type == "user" else "🛟 Оператор"
        body = m.text or ("📎 " + m.content_type)
        parts.append(f"{who}:\n{body}")
    parts.append("━━━━━━━━━━━━━━━━━━")
    return "\n".join(parts)


async def _notify_admin_new_ticket(context, update, ticket, text, content_type) -> None:
    settings = get_settings()
    if not settings.admin_telegram_id:
        return
    extra = ""
    if content_type != CONTENT_TEXT:
        extra = f"📎 Тип: <b>{content_type}</b>\n"
    body = text or "📎 (вложение без подписи)"
    order_line = f"📦 Заказ: <code>#{ticket.order_id}</code>\n" if ticket.order_id else ""
    msg = (
        "🆕 <b>НОВОЕ ОБРАЩЕНИЕ #{}</b>\n".format(ticket.ticket_id) +
        "━━━━━━━━━━━━━━━━━━\n" +
        f"👤 Пользователь: {_user_display(ticket)}\n" +
        f"🆔 ID: <code>{ticket.user_id}</code>\n" +
        order_line +
        f"🕐 Время: {ticket.created_at:%H:%M}\n\n" +
        "💬 <b>Сообщение:</b>\n" + body + "\n" + extra +
        "━━━━━━━━━━━━━━━━━━\n" +
        "📌 Статус: 🟡 Ожидает ответа"
    )
    try:
        await context.bot.send_message(
            chat_id=settings.admin_telegram_id,
            text=msg,
            reply_markup=kb.admin_ticket_actions(ticket.ticket_id),
            parse_mode="HTML",
        )
        # Pass the original rich content (photo/document/video) to the admin so
        # the attachment itself is not lost, not just its caption.
        if content_type != CONTENT_TEXT and update.effective_message is not None:
            try:
                await update.effective_message.forward(settings.admin_telegram_id)
            except Exception as exc:  # pragma: no cover - network errors
                logger.warning("forward rich content failed: %s", exc)
    except Exception as exc:  # pragma: no cover - network errors
        logger.warning("admin notify failed: %s", exc)


# ---------------------------------------------------------------------------
# User: my tickets + view one ticket (ownership enforced)
# ---------------------------------------------------------------------------
async def my_tickets_menu(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    user = update.effective_user
    if user is None:
        return
    _set_state(context, None)
    repo = get_repo(context)
    tickets = await repo.list_user_tickets(user.id)
    if not tickets:
        await safe_send(
            context, update.effective_chat.id,
            "📋 <b>МОИ ОБРАЩЕНИЯ</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "У вас пока нет обращений.\n\n"
            "Создайте обращение, и оно появится здесь.",
            reply_markup=kb.main_menu(is_admin=get_settings().is_admin(user.id)),
        )
        return
    parts = ["📋 <b>МОИ ОБРАЩЕНИЯ</b>", "━━━━━━━━━━━━━━━━━━"]
    for t in tickets:
        order_line = f"📦 Заказ <code>#{t.order_id}</code>" if t.order_id else "📦 Без заказа"
        parts.append(
            f"{kb.status_emoji(t.status)} #{t.ticket_id} · {kb.status_label(t.status)}\n"
            f"{order_line}"
        )
    parts.append("━━━━━━━━━━━━━━━━━━")
    await safe_send(
        context, update.effective_chat.id, "\n".join(parts),
        reply_markup=kb.user_tickets(tickets),
    )


async def view_ticket(update: Update, context: CallbackContext) -> None:
    """Show a ticket + its history to EITHER the owner or the admin."""
    query = update.callback_query
    await answer_query(query)
    parts = kb.parse(query.data)
    if len(parts) < 3 or not parts[2].isdigit():
        await answer_query(query, "Неверное обращение.", alert=True)
        return
    ticket_id = int(parts[2])
    user = update.effective_user
    if user is None:
        return
    is_admin = get_settings().is_admin(user.id)

    repo = get_repo(context)
    ticket = await repo.get_ticket_by_id(ticket_id)
    if ticket is None:
        await safe_send(context, update.effective_chat.id, "Обращение не найдено.")
        return
    # Permission: only the owner OR the admin may view this ticket.
    if ticket.user_id != user.id and not is_admin:
        await answer_query(query, "Это не ваше обращение.", alert=True)
        await safe_send(context, update.effective_chat.id,
                        "❌ <b>Доступ запрещён.</b>")
        return

    messages = await repo.ticket_messages(ticket.id)
    text = _format_history(ticket, messages)
    if is_admin:
        await safe_send(
            context, update.effective_chat.id, text,
            reply_markup=(
                kb.admin_closed_ticket_actions()
                if ticket.status == "closed"
                else kb.admin_ticket_actions(ticket.ticket_id)
            ),
        )
    else:
        await safe_send(
            context, update.effective_chat.id, text,
            reply_markup=kb.user_ticket_actions(ticket.ticket_id),
        )


# ---------------------------------------------------------------------------
# User plain-message reply routing (when not mid-creation)
# ---------------------------------------------------------------------------
async def user_reply(update: Update, context: CallbackContext) -> None:
    """Route a plain message to the user's most recent open ticket."""
    user = update.effective_user
    if user is None:
        return
    message = update.effective_message
    text = (message.text or "").strip() if message else ""
    content_type = CONTENT_TEXT
    if message.photo:
        content_type = CONTENT_PHOTO
        text = message.caption or ""
    elif message.document:
        content_type = CONTENT_DOCUMENT
        text = message.caption or ""
    elif message.video:
        content_type = CONTENT_VIDEO
        text = message.caption or ""

    repo = get_repo(context)
    ticket = await repo.latest_open_ticket(user.id)
    if ticket is None:
        await safe_send(
            context, update.effective_chat.id,
            "📭 У вас нет открытых обращений.\n"
            "Если у вас новый вопрос — создайте новое обращение.",
            reply_markup=kb.main_menu(is_admin=get_settings().is_admin(user.id)),
        )
        return

    await repo.add_message(
        ticket.id, sender_type="user", sender_id=user.id,
        text=text, content_type=content_type,
        telegram_message_id=(message.message_id if message else None),
    )
    await repo.set_ticket_status(ticket.ticket_id, STATUS_WAITING_ADMIN)
    await _notify_admin_user_replied(context, update, ticket, text, content_type)
    await safe_send(
        context, update.effective_chat.id,
        f"✅ Сообщение добавлено в обращение <code>#{ticket.ticket_id}</code>.",
    )


async def _notify_admin_user_replied(context, update, ticket, text, content_type) -> None:
    settings = get_settings()
    if not settings.admin_telegram_id:
        return
    body = text or "📎 (вложение)"
    msg = (
        "💬 <b>ОТВЕТ ПОЛЬЗОВАТЕЛЯ #{}</b>\n".format(ticket.ticket_id) +
        "━━━━━━━━━━━━━━━━━━\n" +
        f"👤 {_user_display(ticket)}\n" +
        (f"📦 Заказ: <code>#{ticket.order_id}</code>\n" if ticket.order_id else "") +
        "Сообщение:\n" + body + "\n" +
        "━━━━━━━━━━━━━━━━━━"
    )
    try:
        await context.bot.send_message(
            chat_id=settings.admin_telegram_id,
            text=msg,
            reply_markup=kb.admin_ticket_actions(ticket.ticket_id),
            parse_mode="HTML",
        )
        if content_type != CONTENT_TEXT and update.effective_message is not None:
            try:
                await update.effective_message.forward(settings.admin_telegram_id)
            except Exception as exc:  # pragma: no cover - network errors
                logger.warning("forward user reply content failed: %s", exc)
    except Exception as exc:  # pragma: no cover - network errors
        logger.warning("admin user-reply notify failed: %s", exc)


# ---------------------------------------------------------------------------
# Admin panel (authorization = ADMIN_TELEGRAM_ID only)
# ---------------------------------------------------------------------------
async def _require_admin(update, query) -> bool:
    user = update.effective_user
    if user is None or not get_settings().is_admin(user.id):
        await answer_query(query, "Доступ запрещён.", alert=True)
        await safe_send(update.effective_chat, "❌ <b>Доступ запрещён.</b>")
        return False
    return True


async def admin_panel(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    if not await _require_admin(update, query):
        return
    repo = get_repo(context)
    tickets = await repo.list_active_tickets()
    if not tickets:
        await safe_send(
            context, update.effective_chat.id,
            "🛟 <b>SUPPORT PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Активных обращений нет.\n"
            "━━━━━━━━━━━━━━━━━━",
            reply_markup=kb.admin_panel(),
        )
        return
    parts = ["🛟 <b>SUPPORT PANEL</b>", "━━━━━━━━━━━━━━━━━━"]
    for t in tickets:
        order_line = f"📦 #{t.order_id}" if t.order_id else "—"
        parts.append(
            f"{kb.status_emoji(t.status)} #{t.ticket_id} · {_user_display(t)}\n"
            f"{order_line}\n"
            f"⏱️ {kb.status_label(t.status)}"
        )
    parts.append("━━━━━━━━━━━━━━━━━━")
    await safe_send(
        context, update.effective_chat.id, "\n".join(parts),
        reply_markup=kb.admin_ticket_list(tickets, back=""),
    )


async def admin_filter(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    if not await _require_admin(update, query):
        return
    parts = kb.parse(query.data)
    if len(parts) < 3:
        return
    status = parts[2]
    repo = get_repo(context)
    if status == "all":
        tickets = await repo.list_active_tickets()
    elif status == "closed":
        tickets = await repo.list_tickets_by_status(STATUS_CLOSED)
    else:
        tickets = await repo.list_tickets_by_status(status)
    if not tickets:
        await safe_send(
            context, update.effective_chat.id,
            "🛟 <b>SUPPORT PANEL</b>\n━━━━━━━━━━━━━━━━━━\nНичего не найдено.\n"
            "━━━━━━━━━━━━━━━━━━",
            reply_markup=kb.admin_panel(),
        )
        return
    parts = ["🛟 <b>SUPPORT PANEL</b>", "━━━━━━━━━━━━━━━━━━"]
    for t in tickets:
        order_line = f"📦 #{t.order_id}" if t.order_id else "—"
        parts.append(
            f"{kb.status_emoji(t.status)} #{t.ticket_id} · {_user_display(t)}\n"
            f"{order_line}\n"
            f"⏱️ {kb.status_label(t.status)}"
        )
    parts.append("━━━━━━━━━━━━━━━━━━")
    await safe_send(
        context, update.effective_chat.id, "\n".join(parts),
        reply_markup=kb.admin_panel(),
    )


async def admin_begin_reply(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    if not await _require_admin(update, query):
        return
    parts = kb.parse(query.data)
    if len(parts) < 3 or not parts[2].isdigit():
        return
    ticket_id = int(parts[2])
    repo = get_repo(context)
    ticket = await repo.get_ticket_by_id(ticket_id)
    if ticket is None:
        await safe_send(context, update.effective_chat.id, "Обращение не найдено.")
        return
    if ticket.status == STATUS_CLOSED:
        await safe_send(
            context, update.effective_chat.id,
            "🔒 Обращение уже закрыто.", reply_markup=kb.admin_panel(),
        )
        return
    context.user_data[ADMIN_REPLY_TICKET] = ticket.ticket_id
    _set_state(context, SupportState.admin_reply)
    await safe_send(
        context, update.effective_chat.id,
        "✏️ <b>ОТВЕТ НА #{}</b>\n".format(ticket.ticket_id) +
        "━━━━━━━━━━━━━━━━━━\n"
        "Введите сообщение для пользователя:\n"
        "━━━━━━━━━━━━━━━━━━",
        reply_markup=kb.creating(),  # reuse "Отмена" as a cancel button
    )


async def admin_reply_typed(update: Update, context: CallbackContext) -> None:
    """Admin's typed text is delivered to the ticket owner automatically."""
    admin = update.effective_user
    if admin is None or not get_settings().is_admin(admin.id):
        return
    ticket_id = context.user_data.pop(ADMIN_REPLY_TICKET, None)
    _set_state(context, None)
    if ticket_id is None:
        await safe_send(
            context, update.effective_chat.id,
            "Не указано, на какое обращение ответить.",
        )
        return
    repo = get_repo(context)
    ticket = await repo.get_ticket_by_id(ticket_id)
    if ticket is None or ticket.status == STATUS_CLOSED:
        await safe_send(
            context, update.effective_chat.id,
            "Обращение уже закрыто, ответ не отправлен.",
            reply_markup=kb.admin_panel(),
        )
        return

    text = (update.effective_message.text or "").strip()
    if not text:
        await safe_send(context, update.effective_chat.id, "Пустое сообщение.")
        return
    await repo.add_message(
        ticket.id, sender_type="admin", sender_id=admin.id,
        text=text, content_type=CONTENT_TEXT, telegram_message_id=None,
    )
    await repo.set_ticket_status(ticket.ticket_id, STATUS_WAITING_USER)
    try:
        await context.bot.send_message(
            chat_id=ticket.user_id,
            text="🛟 <b>ОТВЕТ ОПЕРАТОРА #{}</b>\n".format(ticket.ticket_id) +
                 "━━━━━━━━━━━━━━━━━━\n" +
                 "💬 " + text + "\n" +
                 "━━━━━━━━━━━━━━━━━━\n"
                 "Ответьте здесь, если есть вопросы.",
        )
    except Exception as exc:  # pragma: no cover - network errors
        logger.warning("failed to deliver admin reply: %s", exc)
    await safe_send(
        context, update.effective_chat.id,
        f"✅ Ответ отправлен пользователю (#{ticket.ticket_id}).",
        reply_markup=kb.admin_panel(),
    )


async def admin_close_ticket(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    if not await _require_admin(update, query):
        return
    parts = kb.parse(query.data)
    if len(parts) < 3 or not parts[2].isdigit():
        return
    ticket_id = int(parts[2])
    repo = get_repo(context)
    ticket = await repo.get_ticket_by_id(ticket_id)
    if ticket is None:
        await safe_send(context, update.effective_chat.id, "Обращение не найдено.")
        return
    await repo.set_ticket_status(ticket.ticket_id, STATUS_CLOSED)
    try:
        await context.bot.send_message(
            chat_id=ticket.user_id,
            text="🔒 <b>ОБРАЩЕНИЕ ЗАКРЫТО</b>\n"
                 "━━━━━━━━━━━━━━━━━━\n"
                 f"Ваше обращение <code>#{ticket.ticket_id}</code> закрыто оператором.\n\n"
                 "Если у вас возник новый вопрос, создайте новое обращение.\n"
                 "━━━━━━━━━━━━━━━━━━",
            reply_markup=kb.main_menu(is_admin=False),
        )
    except Exception as exc:  # pragma: no cover - network errors
        logger.warning("failed to notify close: %s", exc)
    await safe_send(
        context, update.effective_chat.id,
        f"🔒 Обращение #{ticket.ticket_id} закрыто.",
        reply_markup=kb.admin_panel(),
    )


# ---------------------------------------------------------------------------
# Text dispatcher based on current state
# ---------------------------------------------------------------------------
async def message_input(update: Update, context: CallbackContext) -> None:
    state = _get_state(context)
    if state == SupportState.creating:
        await receive_ticket_message(update, context)
        return
    if state == SupportState.admin_reply and update.effective_user and \
            get_settings().is_admin(update.effective_user.id):
        await admin_reply_typed(update, context)
        return
    # Everything else: treat plain text from a user as a reply to an open ticket.
    await user_reply(update, context)


def register(app: Application) -> None:
    _ = app
    # Callback handlers (prefix "sup")
    app.add_handler(CallbackQueryHandler(new_ticket_menu, pattern="^" + kb.A_NEW + "$"))
    app.add_handler(CallbackQueryHandler(my_tickets_menu, pattern="^" + kb.A_LIST + "$"))
    app.add_handler(CallbackQueryHandler(main_menu_back, pattern="^" + kb.A_MENU + "$"))
    app.add_handler(CallbackQueryHandler(cancel_creation, pattern="^" + kb.A_CANCEL + "$"))
    app.add_handler(CallbackQueryHandler(view_ticket, pattern="^" + kb.make(kb.A_VIEW, r"\d+") + "$"))
    app.add_handler(CallbackQueryHandler(admin_panel, pattern="^" + kb.A_PANEL + "$"))
    app.add_handler(CallbackQueryHandler(admin_filter, pattern="^" + kb.make(kb.A_FILTER, r"\w+") + "$"))
    app.add_handler(CallbackQueryHandler(admin_begin_reply, pattern="^" + kb.make(kb.A_REPLY, r"\d+") + "$"))
    app.add_handler(CallbackQueryHandler(admin_close_ticket, pattern="^" + kb.make(kb.A_CLOSE, r"\d+") + "$"))
    # Commands
    app.add_handler(CommandHandler("start", start_command))
    # Plain text / photo / document / video messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_input, block=False))
    app.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, message_input, block=False))
    app.add_handler(MessageHandler(filters.Document.ALL & ~filters.COMMAND, message_input, block=False))
    app.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, message_input, block=False))