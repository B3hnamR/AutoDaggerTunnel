from __future__ import annotations

import logging

from telegram.ext import Application, ApplicationBuilder

from .handlers.router import register_handlers
from .runtime import get_job_store, get_store, initialize_runtime
from .settings import Settings, load_settings


def build_app(settings: Settings) -> Application:
    app = ApplicationBuilder().token(settings.bot_token).build()
    initialize_runtime(app, settings)
    register_handlers(app)
    return app


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    settings = load_settings()
    app = build_app(settings)

    get_store(app).init()
    get_job_store(app).init()
    get_job_store(app).mark_running_as_interrupted()

    app.run_polling()


if __name__ == "__main__":
    main()
