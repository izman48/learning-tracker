"""Web entrypoint: `uvicorn --factory learning_tracker.web.main:app`.

Settings are loaded and checked once, at startup. Bad config prints every problem
and exits with status 2 before the server binds a port.
"""

from fastapi import FastAPI

from learning_tracker.cli import require_settings
from learning_tracker.web.app import create_app
from learning_tracker.web.config import WebConfig


def app() -> FastAPI:
    return create_app(WebConfig.from_settings(require_settings("web")))
