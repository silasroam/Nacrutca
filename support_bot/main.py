"""Support Bot entry point.

A fully separate Telegram bot (separate BOT TOKEN) whose only job is user
support. It shares the SAME database as the Traffic Bot (DATABASE_URL) but has
its own repository, handlers and bot token. It runs independently from the
Traffic Bot.

Run with:  python -m support_bot
       or:  python support_bot/run.py
"""
from __future__ import annotations

import asyncio
import logging

from telegram import BotCommand, Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler

from .config import get_settings
from .db import SupportRepository
from .handlers import REPO_KEY, register

logger = logging.getLogger(__name__)


async def _error_handler(update: object, context: object) -> None:
    logger.error("Handler error: %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                update.effective_chat.id,
                "❌ Произошла внутренняя ошибка. Попробуйте ещё раз.",
            )
        except Exception:  # pragma: no cover
            pass


def build_application(repo: SupportRepository) -> Application:
    settings = get_settings()
    app = ApplicationBuilder().token(settings.bot_token).build()
    app.bot_data[REPO_KEY] = repo
    register(app)
    app.add_error_handler(_error_handler)
    return app


BOT_COMMANDS = [
    BotCommand("start", "Открыть меню поддержки"),
]


async def register_bot_commands(app) -> None:
    try:
        await app.bot.set_my_commands(BOT_COMMANDS)
        logger.info("Support bot commands registered.")
    except Exception:  # pragma: no cover - network errors
        logger.exception("Failed to register support bot commands")


def main() -> None:
    settings = get_settings()
    if not settings.bot_token or settings.bot_token == "your_telegram_bot_token_here":
        raise SystemExit(
            "SUPPORT_BOT_TOKEN is not set. Add it to .env and start again."
        )
    if not settings.admin_telegram_id:
        raise SystemExit(
            "ADMIN_TELEGRAM_ID is not set. Add the admin Telegram ID to .env."
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_run_bot(settings))


async def _run_bot(settings) -> None:
    repo = SupportRepository(settings.database_url)
    await repo.init()

    app = build_application(repo)
    await app.initialize()
    await register_bot_commands(app)

    if settings.use_webhook:
        if not settings.webhook_url:
            logger.error("USE_WEBHOOK=true requires WEBHOOK_URL.")
            await app.shutdown()
            return
        await app.bot.set_webhook(settings.webhook_url + settings.webhook_path)
        await app.updater.start_webhook(
            listen="0.0.0.0",
            port=settings.web_port,
            url_path=settings.webhook_path,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    await app.start()
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await app.stop()
        await app.shutdown()
        await repo.close()


if __name__ == "__main__":
    main()