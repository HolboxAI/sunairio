"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import auth
from app.api import routes_auth, routes_health, routes_query
from config.settings import settings
from data import app_db, pools
from llm import client as bedrock

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.prompt_path.is_file():
        logger.error("Prompt file not found: %s", settings.prompt_path)
    app_db.init_db()
    auth.seed_default_admin()
    try:
        pools.init_all()
    except Exception as e:
        logger.warning("Some data backends failed to init: %s", e)
    try:
        bedrock.init_client()
    except Exception as e:
        logger.warning("Bedrock client init failed: %s", e)
    yield
    pools.close_all()


def create_app() -> FastAPI:
    app = FastAPI(title="sunairio-nl2sql", lifespan=lifespan)
    app.include_router(routes_auth.router)
    app.include_router(routes_query.router)
    app.include_router(routes_health.router)
    return app


app = create_app()
