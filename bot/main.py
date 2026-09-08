"""
Traffic Bot entry point (section 20, 26).

Wires the repository, payment providers, traffic provider and all handler
modules together. Starts in polling mode by default; switch to webhook mode via
USE_WEBHOOK=true + WEBHOOK_URL.

NOTE on event loops: `Application.run_polling` / `Application.run_webhook` from
python-telegram-bot v21 are *blocking* methods that manage their own event loop.
Therefore they are called from a synchronous entry point, never from inside an
`asyncio.run(...)` wrapper (which would already be running a loop and raise
"RuntimeError: This event loop is already running").
"""
from __future__ import annotations

import asyncio
import logging

from telegram import BotCommand, Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler

from .config import get_settings
from .database.repository import Repository
from .services.payments import provider_for
from .services.traffic_provider import get_traffic_provider
from .services import pricing as pricing_svc
from .handlers.common import APP_KEY, AppBundle
from .handlers import (
    start,
    traffic,
    orders as orders_h,
    statistics as stats_h,
    payment as payment_h,
    help as help_h,
    admin as admin_h,
)

logger = logging.getLogger(__name__)


def _ensure_bot_data(app, repo, settings) -> None:
    app.bot_data[APP_KEY] = AppBundle(
        repo=repo,
        payment_provider_factory=lambda method, r: provider_for(method, r),
        traffic_provider=get_traffic_provider(repo),
        settings=settings,
    )
    app.bot_data["settings"] = settings
    app.bot_data["limits"] = (settings.min_quantity, settings.max_quantity)


async def _error_handler(update: object, context: object) -> None:
    """Log errors; never leak stack traces to the user (section 26)."""
    logger.error("Handler error: %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                update.effective_chat.id,
                "❌ Произошла внутренняя ошибка. Попробуйте ещё раз или обратитесь "
                "в поддержку.",
            )
        except Exception:  # pragma: no cover
            pass


def build_application(repo) -> Application:
    settings = get_settings()
    app = ApplicationBuilder().token(settings.bot_token).build()

    # Register all handler modules (each exposes register(app)).
    for mod in (start, traffic, payment_h, orders_h, stats_h, help_h, admin_h):
        mod.register(app)

    app.add_error_handler(_error_handler)
    return app


# Standard Telegram bot-command menu shown near the input field (setMyCommands).
# `/start` reuses the already-registered start handler; no second handler added.
BOT_COMMANDS = [
    BotCommand("start", "Старт"),
]


async def register_bot_commands(app) -> None:
    """Register the standard /start command via Bot API (setMyCommands).

    This makes Telegram show `/start — Старт` in the standard command menu next
    to the message input field. Any failure here is logged and non-fatal so the
    bot still starts without the command menu.
    """
    try:
        await app.bot.set_my_commands(BOT_COMMANDS)
        logger.info("Bot commands registered: %s", [c.command for c in BOT_COMMANDS])
    except Exception:  # pragma: no cover - network/API errors
        logger.exception("Failed to register bot commands via setMyCommands")


def main() -> None:
    settings = get_settings()
    if not settings.bot_token or settings.bot_token == "your_telegram_bot_token_here":
        raise SystemExit(
            "BOT_TOKEN is not set. Copy .env.example -> .env and set the token."
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # We run the whole thing inside a single asyncio.run(...) coroutine using the
    # low-level (async) PTB lifecycle. This avoids Application.run_polling /
    # run_webhook, whose internal `asyncio.get_event_loop()` call is broken on
    # Python 3.13 (implicit loop creation was removed).
    asyncio.run(_run_bot(settings))


async def _run_bot(settings) -> None:
    repo = Repository(settings.database_url)
    await repo.init()
    await pricing_svc.seed_default_services(repo)

    app = build_application(repo)
    _ensure_bot_data(app, repo, settings)
    app.bot_data["repo"] = repo

    await app.initialize()

    # Register the standard /start command in the Telegram command menu.
    await register_bot_commands(app)

    if settings.use_webhook:
        if not settings.webhook_url:
            logger.error("USE_WEBHOOK=true requires WEBHOOK_URL in the environment.")
            await app.shutdown()
            return
        await app.bot.set_webhook(settings.webhook_url + settings.webhook_path)
        await app.updater.start_webhook(
            listen="0.0.0.0",
            port=settings.web_port,
            url_path=settings.webhook_path,
            allowed_updates=Update.ALL_TYPES,
        )
        logger.info("Webhook running on %s", settings.webhook_url)
    else:
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("Polling started (dev/staging mode).")

    await app.start()

    try:
        # Run until the process is stopped (Ctrl+C / SIGTERM).
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