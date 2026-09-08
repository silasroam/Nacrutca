import json
import os
from typing import Any, Dict

from bot.config import get_settings
from bot.database.repository import Repository
from bot.main import build_application
from telegram import Update
from telegram.ext import Application

# Global application instance
_application: Application = None

def _get_application() -> Application:
    global _application
    if _application is None:
        settings = get_settings()
        # Initialize repository and build the PTB application
        repo = Repository(settings.database_url)
        _application = build_application(repo)
        # Set webhook if enabled
        if settings.use_webhook:
            webhook_url = settings.webhook_url + settings.webhook_path
            _application.bot.set_webhook(webhook_url)
    return _application

def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # Parse the incoming request
    body = event.get('body')
    if body:
        try:
            update_data = json.loads(body)
            update = Update.de_json(update_data)
            app = _get_application()
            app.process_update(update)
        except Exception:
            # Log error if needed; for now ignore
            pass
    return {
        "statusCode": 200,
        "body": json.dumps({"status": "ok"})
    }