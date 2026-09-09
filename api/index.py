"""
Vercel Python Serverless Function entry point for Telegram Webhook.

This module provides:
- handler: Vercel Python function entry point
- app: WSGI application for ASGI/WSGI support

Handles:
- GET / - Health check endpoint (returns "OK")
- POST /webhook - Telegram webhook endpoint for processing updates
"""
import json
import logging
from typing import Any, Dict

from bot.config import get_settings
from bot.database.repository import Repository
from bot.handlers.common import APP_KEY, AppBundle
from bot.main import build_application
from bot.services.payments import provider_for
from bot.services.traffic_provider import get_traffic_provider
from telegram import Update
from telegram.ext import Application

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global application instance (lazy initialization)
_app: Application = None
_repo: Repository = None


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
        
        # Initialize repository
        _repo = Repository(settings.database_url)
        
        # Build the PTB application
        _app = build_application(_repo)
        
        # Ensure bot data is set up
        _ensure_bot_data(_app, _repo, settings)
        _app.bot_data["repo"] = _repo
        
        logger.info("PTB application initialized")
    
    return _app


def _process_update(update: Update) -> None:
    """Process a Telegram update using the application instance."""
    app = _get_application()
    app.process_update(update)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Vercel Python handler for webhook processing.
    
    Handles:
    - GET / - Health check (returns 200 with "OK")
    - POST /webhook - Telegram webhook (processes updates)
    
    This is the standard Vercel Python entry point pattern.
    """
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
            try:
                update_data = json.loads(body)
                update = Update.de_json(update_data)
                _process_update(update)
            except Exception as e:
                logger.error(f"Error processing webhook: {e}")
        
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


# WSGI application wrapper for Vercel ASGI/WSGI support
class WSGIApp:
    """WSGI application wrapper for the Telegram webhook handler."""
    
    def __call__(self, environ: Dict[str, Any], start_response: Any) -> Any:
        """WSGI callable that delegates to the handler function."""
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


# Export WSGI application for Vercel
app = WSGIApp()