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

# ---------------------------------------------------------------------------
# Texts (aligned with the support service specification)
# ---------------------------------------------------------------------------
WELCOME_TEXT = (
    "🛡️ <b>Nacrutca Support Center</b>\n"
    "\n"
    "Здравствуйте! Вы обратились в официальную службу поддержки сервиса "
    "<b>Nacrutca</b>.\n"
    "• <b>Регламент работы:</b> Наша команда на связи для оперативного "
    "решения любых вопросов, связанных с заказами и услугами.\n"
    "• <b>Время отклика:</b> Обращения обрабатываются в порядке очереди. "
    "Среднее время ответа специалистов составляет <b>до 24 часов</b>.\n"
    "\n"
    "Пожалуйста, опишите вашу проблему или задайте вопрос одним сообщением."
)

ADMIN_TICKET_HEADER = "📩 Новое обращение от пользователя"
ADMIN_CLOSE_HEADER = "❌ Закрытие обращения"

# Confirmation/preview flow for the specified support flow.
TICKET_DRAFT_KEY = "sup_ticket_draft"
PREVIEW_HEADER = "📝 <b>Проверьте ваше обращение:</b>"
CONFIRMED_HEADER = "✅ <b>Ваше обращение успешно отправлено в службу поддержки!</b>"
CONFIRMED_BODY = (
    "Ожидайте ответа (в среднем до 24 часов).\n\n"
    "Если у вас возникнут дополнительные вопросы, вы можете написать нам снова."
)
ADMIN_NOTICE_NEW = "📩 Новое подтвержденное обращение"
CANCELLED_TEXT = "❌ Обращение отменено. Вы можете написать заново или использовать меню."

_MAIN_MENU_TEXT = (
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


async def _resolve_order_id(context, user_id: int, text: str, repo) -> int | None:
    m = _ORDER_REF_RE.search(text)
    if m:
        candidate = int(m.group(1))
        order = await repo.find_order_by_id(candidate)
        if order is not None and order.telegram_user_id == user_id:
            return candidate
    if context.user_data.get(PENDING_ORDER):
        return context.user_data[PENDING_ORDER]
    return None


def _preview_content_text(draft: dict, user) -> str:
    text = draft.get("text") or ""
    content_type = draft.get("content_type")
    if content_type == CONTENT_PHOTO:
        label = "\n📷 <b>Фото</b>"
    elif content_type == CONTENT_DOCUMENT:
        label = "\n📎 <b>Файл</b>"
    elif content_type == CONTENT_VIDEO:
        label = "\n🎬 <b>Видео</b>"
    else:
        label = ""
    order_note = ""
    if draft.get("order_id"):
        order_note = f"\n📦 Заказ: <code>#{draft['order_id']}</code>"
    return (
        PREVIEW_HEADER + "\n"
        + (text if text else "📎 (вложение)")
        + label
        + order_note
    )


def _user_display(ticket) -> str:
    """Human label for a ticket owner, never leaking IDs unless needed."""
    return f"@{ticket.username}" if ticket.username else f"id {ticket.user_id}"


async def start_command(update: Update, context: CallbackContext) -> None:
    """/start entry point.

    Per the support service specification the bot sends a welcome message and
    expects the user to describe their issue in a single message. If a deep
    link like /start ticket_1842 is used, the referenced order is attached to
    the ticket once it is created.
    """
    user = update.effective_user
    if user is None:
        return
    _reset_state(context)
    await get_repo(context).upsert_user(
        user.id, user.username or "", user.first_name or ""
    )

    # Deep link support: /start ticket_<order_id> pre-links a new ticket.
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

    # Welcome message from the support service spec.
    await safe_send(context, update.effective_chat.id, WELCOME_TEXT)

    # Move the user into "describe your issue" mode.
    _set_state(context, SupportState.creating)
    order_hint = ""
    if pending_order:
        order_hint = (
            f"\n📦 Будет привязан заказ <code>#{pending_order}</code>\n"
        )
        await safe_send(
            context,
            update.effective_chat.id,
            "📝 <b>НОВОЕ ОБРАЩЕНИЕ</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Теперь опишите вашу проблему одним сообщением.\n\n"
            + (f"Заказ <code>#{pending_order}</code> будет автоматически привязан к обращению.\n\n" if pending_order else "")
            + "Можно отправить текст, фото или другой поддерживаемый Telegram-контент.\n"
            "Нажмите «❌ Отмена», если передумали.\n"
            "━━━━━━━━━━━━━━━━━━",
            reply_markup=kb.creating(),
        )
    else:
        await safe_send(
            context,
            update.effective_chat.id,
            "📝 <b>НОВОЕ ОБРАЩЕНИЕ</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Опишите вашу проблему одним сообщением.\n"
            "Можно отправить текст, фото или другой поддерживаемый Telegram-контент.\n"
            "Нажмите «❌ Отмена», если передумали.\n"
            "━━━━━━━━━━━━━━━━━━",
            reply_markup=kb.creating(),
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



# ---------------------------------------------------------------------------
# Preview confirmation / cancel (specification support flow)
# ---------------------------------------------------------------------------
async def preview_confirm(update: Update, context: CallbackContext) -> None:
    """User confirmed the preview: create the ticket and notify the admin."""
    query = update.callback_query
    await answer_query(query)
    draft = context.user_data.get(TICKET_DRAFT_KEY)
    if draft is None:
        await safe_send(
            context, update.effective_chat.id,
            "⚠️ Время ожидания истекло. Пожалуйста, начните заново.",
            reply_markup=kb.main_menu(is_admin=get_settings().is_admin(
                update.effective_user.id if update.effective_user else 0,
            )),
        )
        _reset_state(context)
        return

    user = update.effective_user
    repo = get_repo(context)
    ticket = await repo.create_ticket(
        user_id=draft["user_id"],
        username=draft["username"],
        order_id=draft.get("order_id"),
        text=draft["text"],
        content_type=draft["content_type"],
        telegram_message_id=draft.get("telegram_message_id"),
    )
    _reset_state(context)
    context.user_data.pop(PENDING_ORDER, None)
    context.user_data.pop(TICKET_DRAFT_KEY, None)

    # Replace the preview message with the confirmation message.
    success_text = CONFIRMED_HEADER + "\n" + CONFIRMED_BODY
    try:
        await context.bot.edit_message_text(
            chat_id=user.id,
            message_id=draft.get("preview_message_id", 0),
            text=success_text,
            parse_mode="HTML",
        )
    except Exception as exc:  # pragma: no cover - network / message changes
        logger.warning("preview confirm edit failed, sending new message: %s", exc)
        try:
            await context.bot.send_message(
                chat_id=user.id,
                text=success_text,
                parse_mode="HTML",
            )
        except Exception:
            pass

    await safe_send(
        context,
        user.id,
        text=(
            f"🆔 Ваше обращение: <code>#{ticket.ticket_id}</code>\\n"
            "Оператор ответит в ближайшее время. Ответ придёт сюда автоматически."
        ),
        reply_markup=kb.main_menu(is_admin=get_settings().is_admin(user.id)),
        parse_mode="HTML",
    )

    await _notify_admin_new_ticket(context, update, ticket, draft["text"],
                                  draft["content_type"])


async def preview_cancel(update: Update, context: CallbackContext) -> None:
    """User cancelled the preview (❌ Отменить)."""
    query = update.callback_query
    await answer_query(query)
    draft = context.user_data.get(TICKET_DRAFT_KEY) or {}
    preview_message_id = draft.get("preview_message_id")

    _reset_state(context)
    context.user_data.pop(TICKET_DRAFT_KEY, None)
    context.user_data.pop(PENDING_ORDER, None)

    if preview_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=query.effective_chat.id,
                message_id=preview_message_id,
                text=CANCELLED_TEXT,
                parse_mode="HTML",
            )
        except Exception as exc:  # pragma: no cover - already deleted / changed
            logger.warning("preview cancel edit failed: %s", exc)


async def _notify_admin_new_ticket(context, update, ticket, text, content_type) -> None:
    """Notify admin about a newly confirmed ticket.

    Matches the support service spec:
        📩 Новое подтвержденное обращение
        👤 От: @username (ID: 123456789)
        📝 Текст: [Текст пользователя]
        [Кнопки для админа: 💬 Ответить / ❌ Закрыть]
    """
    settings = get_settings()
    if not settings.admin_telegram_id:
        return

    body = text or "📎 (вложение)"
    msg = (
        ADMIN_NOTICE_NEW + "\n"
        + "\n"
        + "👤 От: " + (f"@{ticket.username}" if ticket.username else "(no username)") + f" (ID: {ticket.user_id})\n"
        + "\n"
        + "📝 Текст: " + body + "\n"
    )
    try:
        await context.bot.send_message(
            chat_id=settings.admin_telegram_id,
            text=msg,
            reply_markup=kb.admin_ticket_actions_ticket(ticket.ticket_id),
            parse_mode="HTML",
        )
        if content_type != CONTENT_TEXT and update.effective_message is not None:
            try:
                await update.effective_message.forward(settings.admin_telegram_id)
            except Exception as exc:  # pragma: no cover - network errors
                logger.warning("forward new ticket content failed: %s", exc)
    except Exception as exc:  # pragma: no cover - network errors
        logger.warning("admin new-ticket notify failed: %s", exc)

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
    """Handle the user message that describes a new ticket.

    Per the support flow spec the message is NOT sent to the admin immediately.
    Instead the bot stores a draft, shows a preview with confirm/cancel buttons,
    and only notifies the admin after the user confirms.
    """
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
            "⚠️ Пожалуйста, отправьте текстовое описание вашей проблемы или \n"
            "фото/файл с текстом.", reply_markup=kb.creating(),
        )
        return



    repo = get_repo(context)
    order_id = _resolve_order_id(context, user.id, text, repo)

    # Store a draft so the user can review before sending.
    draft = {
        "user_id": user.id,
        "username": user.username or "",
        "order_id": order_id,
        "text": text,
        "content_type": content_type,
        "telegram_message_id": telegram_message_id,
    }
    context.user_data[TICKET_DRAFT_KEY] = draft

    preview_text = _preview_content_text(draft, user)
    sent = await safe_send(
        context, update.effective_chat.id, preview_text,
        reply_markup=kb.preview_keyboard(),
    )
    if sent:
        draft["preview_message_id"] = sent.message_id

    _set_state(context, SupportState.previewing)


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
            reply_markup=kb.admin_ticket_actions_ticket(ticket.ticket_id),
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
    app.add_handler(CallbackQueryHandler(preview_confirm, pattern="^" + kb.A_CONFIRM + "$"))
    app.add_handler(CallbackQueryHandler(preview_cancel, pattern="^" + kb.A_CANCEL_PREVIEW + "$"))
    # Commands
    app.add_handler(CommandHandler("start", start_command))
    # Plain text / photo / document / video messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_input, block=False))
    app.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, message_input, block=False))
    app.add_handler(MessageHandler(filters.Document.ALL & ~filters.COMMAND, message_input, block=False))
    app.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, message_input, block=False))