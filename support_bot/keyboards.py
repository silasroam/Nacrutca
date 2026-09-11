"""Inline keyboards for the Support Bot.

Callback data only ever carries an identifier (ticket_id / status/action).
The server re-loads every referenced ticket from the DB and enforces ownership
and admin permission on each callback — callback payloads are NEVER trusted for
authorization or data decisions.

Prefix ``sup`` keeps payloads isolated from other bots/apps.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

P = "sup"
SEP = ":"


def make(*parts) -> str:
    return SEP.join(str(p) for p in parts)


def parse(data: str) -> list[str]:
    return list(data.split(SEP))


# Action prefixes
A_NEW = make(P, "new")
A_LIST = make(P, "list")
A_MENU = make(P, "menu")
A_CANCEL = make(P, "cancel")
A_VIEW = make(P, "t")          # t:<ticket_id>
A_PANEL = make(P, "panel")
A_FILTER = make(P, "f")        # f:<status|all|closed>
A_REPLY = make(P, "reply")     # reply:<ticket_id>
A_CLOSE = make(P, "close")     # close:<ticket_id>
A_CONFIRM = make(P, "confirm")
A_CANCEL_PREVIEW = make(P, "cancel_preview")


def status_emoji(status: str) -> str:
    return {
        "open": "🟢",
        "waiting_admin": "🟡",
        "waiting_user": "🟢",
        "closed": "⚫",
    }.get(status, "🟡")


def status_label(status: str) -> str:
    return {
        "open": "Открыто",
        "waiting_admin": "Ожидает ответа",
        "waiting_user": "Ожидает ответа пользователя",
        "closed": "Закрыто",
    }.get(status, status)


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📝 Создать обращение", callback_data=A_NEW)],
        [InlineKeyboardButton("📋 Мои обращения", callback_data=A_LIST)],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("🛟 Support Panel", callback_data=A_PANEL)])
    rows.append([InlineKeyboardButton("🏠 Главное меню", callback_data=A_MENU)])
    return InlineKeyboardMarkup(rows)


def creating() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Отмена", callback_data=A_CANCEL)]]
    )


def user_tickets(tickets: list) -> InlineKeyboardMarkup:
    rows = []
    for t in tickets:
        label = f"{status_emoji(t.status)} #{t.ticket_id} · {status_label(t.status)}"
        rows.append(
            [InlineKeyboardButton(label, callback_data=make(A_VIEW, t.ticket_id))]
        )
    rows.append([InlineKeyboardButton("🔙 Главное меню", callback_data=A_MENU)])
    return InlineKeyboardMarkup(rows)


def user_ticket_actions(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔙 Мои обращения", callback_data=A_LIST)],
            [InlineKeyboardButton("🏠 Главное меню", callback_data=A_MENU)],
        ]
    )


def admin_panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🟡 Ожидают меня", callback_data=make(A_FILTER, "waiting_admin"))],
            [InlineKeyboardButton("🟢 Ожидают пользователя", callback_data=make(A_FILTER, "waiting_user"))],
            [InlineKeyboardButton("📋 Все открытые", callback_data=make(A_FILTER, "all"))],
            [InlineKeyboardButton("⚫ Закрытые", callback_data=make(A_FILTER, "closed"))],
            [InlineKeyboardButton("🏠 Главное меню", callback_data=A_MENU)],
        ]
    )


def admin_ticket_list(tickets: list, back: str) -> InlineKeyboardMarkup:
    rows = []
    for t in tickets:
        label = f"{status_emoji(t.status)} #{t.ticket_id} · @{t.username or str(t.user_id)}"
        rows.append(
            [InlineKeyboardButton(label, callback_data=make(A_VIEW, t.ticket_id))]
        )
    rows.append([InlineKeyboardButton("🔙 Support Panel", callback_data=A_PANEL)])
    if back:
        rows.append([InlineKeyboardButton("🔙 Назад", callback_data=back)])
    return InlineKeyboardMarkup(rows)


def admin_ticket_actions(ticket_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("💬 Ответить", callback_data=make(A_REPLY, ticket_id))],
        [InlineKeyboardButton("🔒 Закрыть", callback_data=make(A_CLOSE, ticket_id))],
        [InlineKeyboardButton("🔙 Support Panel", callback_data=A_PANEL)],
    ]
    return InlineKeyboardMarkup(rows)

def admin_ticket_actions_ticket(ticket_id: int) -> InlineKeyboardMarkup:
    """Actions shown under a new-ticket notification for the admin.

    Matches the support service spec:
        [ 💬 Ответить ] [ ❌ Закрыть диалог ]
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💬 Ответить", callback_data=make(A_REPLY, ticket_id)),
                InlineKeyboardButton("❌ Закрыть диалог", callback_data=make(A_CLOSE, ticket_id)),
            ],
        ]
    )


def preview_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard shown under the ticket preview.

    Matches the support flow spec:
        [ ✅ Подтвердить отправку ] [ ❌ Отменить ]
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Подтвердить отправку", callback_data=A_CONFIRM),
                InlineKeyboardButton("❌ Отменить", callback_data=A_CANCEL_PREVIEW),
            ],
        ]
    )



def admin_closed_ticket_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 Support Panel", callback_data=A_PANEL)]]
    )