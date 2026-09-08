"""/start and main-menu handling (sections 1-2)."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CallbackContext, CommandHandler, CallbackQueryHandler

from ..keyboards import constants as C
from ..keyboards import main as kb
from ..states.order import OrderState
from .common import (
    get_repo,
    set_state,
    reset_flow,
    answer_query,
    safe_answer,
)

logger = logging.getLogger(__name__)

WELCOME = (
    "⚡️ <b>Traffic Bot</b>\n\n"
    "Добро пожаловать!\n\n"
    "Наш сервис предоставляет профессиональные инструменты для продвижения "
    "и закупки целевого трафика по выгодным тарифам. Отслеживайте аналитику "
    "и управляйте кампаниями в режиме реального времени.\n\n"
    "Поддерживаемые платформы:\n"
    "📍 Telegram\n"
    "📍 TikTok\n"
    "📍 Instagram\n"
    "📍 YouTube\n\n"
    "— Выберите необходимое действие ниже:"
)


async def start_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if user is None:
        return
    await get_repo(context).upsert_user(user.id, user.username or "", user.first_name or "")
    reset_flow(context)
    set_state(context, OrderState.MAIN_MENU)
    await safe_answer(
        context, update.effective_chat.id, WELCOME,
        reply_markup=kb.main_menu(),
    )


async def _export_menu(context, chat_id):
    set_state(context, OrderState.MAIN_MENU)
    await safe_answer(context, chat_id, WELCOME, reply_markup=kb.main_menu())


async def main_menu_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    await _export_menu(context, update.effective_chat.id)


def register(app) -> None:
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(main_menu_callback, pattern=f"^{C.CB_BACK}$"))