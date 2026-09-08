"""Statistics section (16)."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CallbackContext, CallbackQueryHandler

from ..keyboards import constants as C
from ..keyboards import statistics as kb_stats
from .common import get_repo, answer_query, safe_answer

logger = logging.getLogger(__name__)


def _fmt(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def _range_days(key: str) -> int | None:
    return {"today": 1, "7": 7, "30": 30, "all": None}.get(key, None)


async def my_stats(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    parts = query.data.split(C.SEP)          # cb:stat[:<period>]
    period = parts[2] if len(parts) > 2 else "all"
    days = _range_days(period)
    repo = get_repo(context)
    user = update.effective_user
    # Only fully completed orders are shown in "Моя статистика".
    stats = await repo.user_stats(user.id, days=days, completed_only=True)

    if stats["total_orders"] == 0:
        text = (
            "📊 <b>МОЯ СТАТИСТИКА</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📭 <b>У вас пока нет выполненных заказов.</b>\n\n"
            "Статистика появится после первого завершённого заказа.\n"
            "━━━━━━━━━━━━━━━━━━"
        )
        await safe_answer(
            context, update.effective_chat.id, text,
            reply_markup=kb_stats.stats_empty(),
        )
        return

    text = (
        "📊 <b>МОЯ СТАТИСТИКА</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"✅ Выполнено заказов: <b>{_fmt(stats['completed'])}</b>\n"
        f"📦 Всего единиц: <b>{_fmt(stats['units'])}</b>\n"
        f"💰 Потрачено: <b>{_fmt(stats['spent'])} ₽</b>\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    await safe_answer(
        context, update.effective_chat.id, text,
        reply_markup=kb_stats.stat_periods(),
    )


def register(app) -> None:
    app.add_handler(
        CallbackQueryHandler(my_stats, pattern="^" + C.CB_STATS + "(:" + ".*)?$")
    )
    app.add_handler(
        CallbackQueryHandler(my_stats, pattern="^" + C.CB_STAT_PERIOD + ":")
    )