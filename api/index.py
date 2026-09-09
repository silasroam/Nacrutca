"""
Vercel Python Serverless Function entry point for Telegram Webhook.

This module provides:
- handler: Vercel Python function entry point
- app: WSGI application for ASGI/WSGI support

Handles:
- GET / - Health check endpoint (returns "OK")
- POST /webhook - Telegram webhook endpoint for processing updates
"""
import asyncio
import json
import logging
import sys
import traceback
from typing import Any, Dict

from bot.config import get_settings
from bot.database.repository import Repository
from bot.handlers.common import APP_KEY, AppBundle
from bot.main import build_application
from bot.services.payments import provider_for
from bot.services.traffic_provider import get_traffic_provider
from telegram import Update
from telegram.ext import Application

# Configure logging to stderr for Vercel
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# Global application instance (lazy initialization)
_app: Application = None
_repo: Repository = None
_app_initialized: bool = False


def _ensure_bot_data(app: Application, repo: Repository, settings) -> None:
    """Ensure bot data is properly set up."""
    app.bot_data[APP_KEY] = AppBundle(
        repo=repo,
        payment_provider_factory=lambda method, r: provider_for(method, r),
        traffic_provider=get_traffic_provider(repo),
        settings=settings,
    )
    app.bot_data["settings"] = settings
    app.bot_data["limits"] = (settings.min_quantity, settings.max_quantity)


def _get_application() -> Application:
    """Get or create the PTB application instance (lazy initialization)."""
    global _app, _repo
    
    if _app is None:
        settings = get_settings()
        
        # Fail fast with a clear message instead of letting ApplicationBuilder
        # raise a confusing low-level error (or, worse, crash the cold module
        # import on Vercel). This is a *lazy* check: it only runs on the first
        # webhook call, never at module import time.
        if not settings.bot_token or settings.bot_token == "your_telegram_bot_token_here":
            raise RuntimeError(
                "BOT_TOKEN is not set or is invalid. Configure the BOT_TOKEN "
                "environment variable in Vercel before invoking /webhook."
            )
        
        # Initialize repository (also lazy - the engine is only created here,
        # not at import time, so a missing DATABASE_URL won't crash the module).
        _repo = Repository(settings.database_url)
        
        # Build the PTB application
        _app = build_application(_repo)
        
        # Ensure bot data is set up
        _ensure_bot_data(_app, _repo, settings)
        _app.bot_data["repo"] = _repo
        
        logger.info("PTB application instance created")
    
    return _app


async def _ensure_application_running() -> Application:
    """
    Return a fully-initialized PTB application.

    python-telegram-bot v21 requires ``Application.initialize()`` (and
    ``Application.start()`` for handler jobs/updates) to be called ONCE before
    ``Application.process_update()``. Without it, ``process_update`` raises::

        RuntimeError: This Application was not initialized via `Application.initialize`!

    That error was being swallowed by the webhook handler and acknowledged with
    a 200, so Telegram never redelivered and the bot silently ignored updates.

    We run the (async) lifecycle here exactly once, guarded by a module flag,
    because the application is a long-lived global reused between requests.
    """
    global _app_initialized

    app = _get_application()

    # The module-level flag is the single source of truth: the application is a
    # long-lived global reused across requests, so the (async) lifecycle must be
    # run exactly once. ``running`` is a real attribute on Application in v21;
    # there is no public ``initialized`` attribute to inspect, hence the flag.
    if not _app_initialized:
        if not app.running:
            await app.initialize()
            await app.start()
        _app_initialized = True
        logger.info("PTB application lifecycle initialized and started")

    return app


async def _process_update_async(update: Update) -> None:
    """Process a Telegram update asynchronously using the application instance."""
    app = await _ensure_application_running()
    await app.process_update(update)


def _process_update(update: Update) -> None:
    """Process a Telegram update using the application instance (synchronous wrapper)."""
    try:
        # Run the async process_update in a new event loop
        asyncio.run(_process_update_async(update))
    except RuntimeError as e:
        # Handle "Event loop is already running" error
        if "Event loop is already running" in str(e):
            # Use the existing event loop
            loop = asyncio.get_event_loop()
            loop.run_until_complete(_process_update_async(update))
        else:
            raise


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Vercel Python handler for webhook processing.
    
    Handles:
    - GET / - Health check (returns 200 with "OK")
    - POST /webhook - Telegram webhook (processes updates)
    
    This is the standard Vercel Python entry point pattern.
    """
    try:
        method = event.get("httpMethod", "GET")
        path = event.get("path", "/")
        
        # Health check endpoint - GET /
        if method == "GET" and path == "/":
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "text/plain"},
                "body": "OK"
            }
        
        # Process webhook POST request - POST /webhook
        if method == "POST" and path == "/webhook":
            body = event.get("body")
            if body:
                # Parse JSON and build the application OUTSIDE the per-update
                # try, so that a catastrophic failure here (malformed body,
                # missing/invalid BOT_TOKEN, DB engine init error) propagates
                # up to the global 500 JSON handler below instead of being
                # silently acknowledged with a 200.
                update_data = json.loads(body)
                app = _get_application()
                update = Update.de_json(update_data, app.bot)

                # Only genuine per-update Telegram processing errors (with a
                # fully-initialized application) are caught here and acknowledged
                # with a quick 200 so Telegram uses its normal retry semantics.
                try:
                    _process_update(update)
                except Exception as e:
                    logger.error(f"Error processing webhook: {e}")
                    logger.error(traceback.format_exc())
            
            # Always return 200 OK quickly to avoid webhook timeout
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "text/plain"},
                "body": "OK"
            }
        
        # Method not allowed or invalid path
        return {
            "statusCode": 405,
            "headers": {"Content-Type": "text/plain"},
            "body": "Method Not Allowed"
        }
    except Exception as e:
        # Global safety net: log the full traceback to stderr and return a
        # valid JSON 500 response INSTEAD of crashing the serverless process.
        # Without this, any uncaught error would abort the Vercel invocation
        # with FUNCTION_INVOCATION_FAILED and return an unusable HTTP 500 body.
        print(traceback.format_exc(), file=sys.stderr)
        logger.error("Unhandled error in handler: %s", e, exc_info=True)
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": str(e)}),
        }


# WSGI application wrapper for Vercel ASGI/WSGI support
class WSGIApp:
    """WSGI application wrapper for the Telegram webhook handler."""
    
    def __call__(self, environ: Dict[str, Any], start_response: Any) -> Any:
        """WSGI callable that delegates to the handler function."""
        try:
            method = environ.get("REQUEST_METHOD", "GET")
            path = environ.get("PATH_INFO", "/")
            
            # Build event dict from WSGI environ
            event = {
                "httpMethod": method,
                "path": path,
            }
            
            # Read request body for POST requests
            if method == "POST":
                try:
                    content_length = int(environ.get("CONTENT_LENGTH", 0))
                    if content_length > 0:
                        body = environ["wsgi.input"].read(content_length).decode("utf-8")
                        event["body"] = body
                except Exception as e:
                    logger.error(f"Error reading request body: {e}")
                    logger.error(traceback.format_exc())
            
            # Call the handler function
            response = handler(event, None)
            
            # Build WSGI response
            status_code = response.get("statusCode", 200)
            headers = response.get("headers", {})
            body = response.get("body", "")
            
            status = f"{status_code} {'OK' if status_code == 200 else 'Error'}"
            response_headers = [(k, v) for k, v in headers.items()]
            start_response(status, response_headers)
            
            return [body.encode("utf-8")]
        except Exception as e:
            # Global safety net for the WSGI path: log the full traceback to
            # stderr and return a valid JSON 500 response instead of letting
            # the exception propagate up and crash the serverless process.
            print(traceback.format_exc(), file=sys.stderr)
            logger.error("Unhandled error in WSGIApp: %s", e, exc_info=True)
            payload = json.dumps({"error": str(e)}).encode("utf-8")
            start_response("500 Error", [("Content-Type", "application/json")])
            return [payload]


# Export WSGI application for Vercel
app = WSGIApp()