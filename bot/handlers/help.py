"""Help section (17)."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CallbackContext, CallbackQueryHandler

from ..keyboards import constants as C
from ..keyboards import payments as kb_payments
from .common import answer_query, safe_answer, set_state
from ..states.order import OrderState

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "ℹ️ <b>Справочный центр</b>\n\n"
    "Traffic Bot — автоматизированная система для запуска и масштабирования "
    "трафика в Telegram, TikTok, Instagram и YouTube.\n\n"
    "📋 Порядок оформления заказа:\n\n"
    "1. Выберите платформу и услугу.\n"
    "2. Укажите объём и ссылку на объект.\n"
    "3. Выберите удобный способ оплаты (Криптовалюта / Telegram Stars).\n"
    "4. Подтвердите и оплатите счёт.\n"
    "5. Отслеживайте процесс выполнения в разделе «📋 Мои заказы».\n\n"
    "— Если у вас возникли вопросы или задержка платежа, свяжитесь со службой поддержки:"
)


async def help_screen(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    set_state(context, OrderState.MAIN_MENU)
    await safe_answer(
        context, update.effective_chat.id, HELP_TEXT, reply_markup=kb_payments.support()
    )


def register(app) -> None:
    app.add_handler(CallbackQueryHandler(help_screen, pattern="^" + C.CB_HELP + "$"))