"""
Shared handler helpers (FSM management, safe access to shared data).

The bot keeps per-user state in `context.user_data` and a single shared
`context.bot_data["app"]` object that holds the repository and the
payment/traffic providers.

FSM (section 19): `set_state` / `get_state` / `clear_draft`. On cancel we
always reset the draft state to MAIN_MENU so no half-built order leaks into
the next flow.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CallbackContext, ContextTypes

from ..states.order import OrderState

logger = logging.getLogger(__name__)

# key under bot_data that stores the AppContext bundle
APP_KEY = "app"


class AppBundle:
    """Everything the handlers need beyond the bot itself."""

    def __init__(self, repo, payment_provider_factory, traffic_provider, settings):
        self.repo = repo
        self.payment_provider_factory = payment_provider_factory
        self.traffic_provider = traffic_provider
        self.settings = settings


def get_app(context: ContextTypes.DEFAULT_TYPE) -> AppBundle:
    return context.bot_data[APP_KEY]


def get_repo(context: ContextTypes.DEFAULT_TYPE):
    return get_app(context).repo


# ---------------------------------------------------------------------------
# FSM helpers
# ---------------------------------------------------------------------------
def set_state(context: ContextTypes.DEFAULT_TYPE, state: str) -> None:
    context.user_data["state"] = state


def get_state(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("state", OrderState.MAIN_MENU)


def reset_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Abort the current order draft and return to the main menu state."""
    context.user_data.pop("draft", None)
    context.user_data.pop("pending_service_id", None)
    context.user_data["state"] = OrderState.MAIN_MENU


def ensure_draft(context: ContextTypes.DEFAULT_TYPE) -> dict:
    draft = context.user_data.get("draft")
    if not isinstance(draft, dict):
        draft = context.user_data["draft"] = {}
    return draft


# ---------------------------------------------------------------------------
# Error / UX helpers (section 25)
# ---------------------------------------------------------------------------
def ux_error(message: str) -> str:
    return f"❌ {message}"


async def safe_answer(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str,
                      reply_markup=None, parse_mode: str = "HTML",
                      edit: bool = False, message_id: int | None = None):
    """Send or edit a message, swallowing irrelevant errors."""
    try:
        if edit and message_id:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text,
                reply_markup=reply_markup, parse_mode=parse_mode,
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id, text=text, reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
    except Exception as exc:  # pragma: no cover - network/telegram errors
        logger.warning("safe_answer failed: %s", exc)


async def answer_query(query, text: str | None = None, alert: bool = False,
                       ) -> None:
    try:
        await query.answer(text=text, show_alert=alert)
    except Exception as exc:  # pragma: no cover
        logger.warning("answer_query failed: %s", exc)