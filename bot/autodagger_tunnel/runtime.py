from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict

from telegram.ext import Application

from .db import JobStore, ServerStore
from .security import load_or_create_fernet
from .settings import Settings


BOTDATA_SETTINGS = "settings"
BOTDATA_STORE = "store"
BOTDATA_JOB_STORE = "job_store"
BOTDATA_ACTIVE_JOBS = "active_jobs"


@dataclass
class ActiveJobContext:
    job_id: str
    chat_id: int
    mode: str
    stop_event: asyncio.Event
    task: asyncio.Task | None = None


def initialize_runtime(app: Application, settings: Settings) -> None:
    fernet = load_or_create_fernet(settings.key_file)
    store = ServerStore(settings.db_path, fernet)
    job_store = JobStore(settings.job_db_path)

    app.bot_data[BOTDATA_SETTINGS] = settings
    app.bot_data[BOTDATA_STORE] = store
    app.bot_data[BOTDATA_JOB_STORE] = job_store
    app.bot_data[BOTDATA_ACTIVE_JOBS] = {}


def _app_from(source: object) -> Application:
    if isinstance(source, Application):
        return source
    app = getattr(source, "application", None)
    if isinstance(app, Application):
        return app
    raise TypeError("Expected telegram Application or object with .application")


def get_settings(source: object) -> Settings:
    app = _app_from(source)
    return app.bot_data[BOTDATA_SETTINGS]


def get_store(source: object) -> ServerStore:
    app = _app_from(source)
    return app.bot_data[BOTDATA_STORE]


def get_job_store(source: object) -> JobStore:
    app = _app_from(source)
    return app.bot_data[BOTDATA_JOB_STORE]


def get_active_jobs(source: object) -> Dict[int, ActiveJobContext]:
    app = _app_from(source)
    return app.bot_data[BOTDATA_ACTIVE_JOBS]
