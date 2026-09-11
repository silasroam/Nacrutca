"""Help section (17)."""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext, CallbackQueryHandler

from ..keyboards import constants as C
from ..keyboards import payments as kb_payments
from .common import answer_query, safe_answer, set_state
from ..states.order import OrderState
from ..config import get_settings

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "🛡️ <b>Гарантии и правила сервиса</b>\n\n"
    "• Мы полностью несём ответственность за качество и выполнение каждого заказа. "
    "Все услуги оказываются строго в рамках заявленных стандартов, а любые "
    "непредвиденные ситуации решаются в пользу клиента через нашу службу поддержки.\n\n"
    "• <b>Сроки выполнения:</b>\n"
    "Стандартный процесс выполнения заказа занимает до 72 часов. Если по истечении "
    "этого времени статус заказа не изменился или у вас возникли вопросы, пожалуйста, "
    "обратитесь в поддержку — мы оперативно проверим информацию и поможем решить ситуацию.\n\n"
    "• <b>Качество и безопасность:</b>\n"
    "Для выполнения заказов используются специально подготовленные (\"прогретые\") "
    "аккаунты премиум-класса. Они корректно проходят алгоритмические проверки платформ, "
    "что позволяет защитить ваш канал от блокировок, теневого бана или резких просадок "
    "статистики по активности.\n\n"
    "• <b>Порядок при ошибках с криптовалютой:</b>\n"
    "Если по каким-либо причинам платеж в криптовалюте не был автоматически засчитан "
    "ботом, пожалуйста, не переживайте. Обратитесь в службу поддержки и предоставьте "
    "следующие данные для оперативного зачисления средств:\n"
    "  — Номер заказа или скриншот квитанции/выписки (если сохранился).\n"
    "  — Хэш транзакции (TXID) или точный идентификатор перевода.\n"
    "  — Точную сумму пополнения и название криптовалюты (например, TON, TRX, SOL, LTC).\n\n"
    "— Если у вас остались вопросы, свяжитесь со службой поддержки:"
)


async def help_screen(update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    set_state(context, OrderState.MAIN_MENU)
    await safe_answer(
        context, update.effective_chat.id, HELP_TEXT, reply_markup=kb_payments.support()
    )


async def rules_callback(update, context: CallbackContext) -> None:
    """Show rules & guarantees + support URL button."""
    query = update.callback_query
    await answer_query(query)
    settings = get_settings()
    username = settings.support_bot_username

    text = (
        "🛡️ <b>Правила и гарантии сервиса</b>\n\n"
        "• Мы полностью несём ответственность за качество и выполнение каждого заказа. "
        "Все услуги оказываются строго в рамках заявленных стандартов, а любые "
        "непредвиденные ситуации решаются в пользу клиента через нашу службу поддержки.\n\n"
        "• <b>Сроки выполнения:</b>\n"
        "Стандартный процесс выполнения заказа занимает до 72 часов. Если по истечении "
        "этого времени статус заказа не изменился или у вас возникли вопросы, пожалуйста, "
        "обратитесь в поддержку — мы оперативно проверим информацию и поможем решить ситуацию.\n\n"
        "• <b>Качество и безопасность:</b>\n"
        "Для выполнения заказов используются специально подготовленные (\"прогретые\") "
        "аккаунты премиум-класса. Они корректно проходят алгоритмические проверки платформ, "
        "что позволяет защитить ваш канал от блокировок, теневого бана или резких просадок "
        "статистики по активности.\n\n"
        "• <b>Порядок при ошибках с криптовалютой:</b>\n"
        "Если по каким-либо причинам платеж в криптовалюте не был автоматически засчитан "
        "ботом, пожалуйста, не переживайте. Обратитесь в службу поддержки и предоставьте "
        "следующие данные для оперативного зачисления средств:\n"
        "  — Номер заказа или скриншот квитанции/выписки (если сохранился).\n"
        "  — Хэш транзакции (TXID) или точный идентификатор перевода.\n"
        "  — Точную сумму пополнения и название криптовалюты (например, TON, TRX, SOL, LTC).\n\n"
        "— Если у вас остались вопросы, свяжитесь со службой поддержки:"
    )

    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💬 Поддержка", url=f"https://t.me/{username}"),
                InlineKeyboardButton("🔙 Назад", callback_data=C.CB_BACK),
            ]
        ]
    )

    await safe_answer(context, update.effective_chat.id, text, reply_markup=markup)


async def support_callback(update, context: CallbackContext) -> None:
    """Show support contact screen with URL button to the support bot."""
    query = update.callback_query
    await answer_query(query)
    settings = get_settings()
    username = settings.support_bot_username

    if username:
        text = (
            "🛟 <b>Служба поддержки</b>\n\n"
            "Нажмите кнопку ниже, чтобы написать в поддержку:"
        )
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("💬 Поддержка", url=f"https://t.me/{username}")]]
        )
    else:
        text = (
            "🛟 <b>Служба поддержки</b>\n\n"
            "Для связи с нами обращайтесь через основного бота.\n\n"
            "Мы ответим в течение 24 часов."
        )
        markup = kb_payments.support()

    await safe_answer(context, update.effective_chat.id, text, reply_markup=markup)


def register(app) -> None:
    app.add_handler(CallbackQueryHandler(help_screen, pattern="^" + C.CB_HELP + "$"))
    app.add_handler(CallbackQueryHandler(support_callback, pattern="^" + C.CB_SUPPORT + "$"))
    app.add_handler(CallbackQueryHandler(rules_callback, pattern="^" + C.CB_RULES + "$"))
