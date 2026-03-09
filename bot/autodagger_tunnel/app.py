import logging
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
)

from .settings import Settings, load_settings
from .db import ServerStore, JobStore
from .security import load_or_create_fernet

from .handlers.start import start_command, help_command, whoami_command, cancel, restart_menu_from_conversation
from .handlers.servers_handlers import (
    ADD_NAME, ADD_HOST, ADD_USERNAME, ADD_PASSWORD,
    EDIT_NAME, EDIT_HOST, EDIT_USERNAME, EDIT_PASSWORD,
    list_servers_button,
    add_start, add_name, add_host, add_username, add_password,
    edit_server_callback, edit_name, edit_host, edit_username, edit_password,
    delete_server_callback
)
from .handlers.jobs_handlers import (
    TEST_TRANSPORT, TEST_TARGET,
    test_start, test_receive_transport, test_receive_target,
    stop_current_job, resume_command
)
from .utils.ui import (
    BTN_ADD, BTN_LIST, BTN_TEST, BTN_STOP,
    STATE_TEXT_FILTER, MENU_BUTTON_FILTER
)

def build_app(settings: Settings) -> ApplicationBuilder:
    app = ApplicationBuilder().token(settings.bot_token).build()

    fernet = load_or_create_fernet(settings.key_file)
    store = ServerStore(settings.db_path, fernet)
    job_store = JobStore(settings.job_db_path)
    # Expose configs globally to app context
    app.bot_data["settings"] = settings
    app.bot_data["store"] = store
    app.bot_data["job_store"] = job_store
    app.bot_data["active_jobs"] = {}

    # Basic Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("whoami", whoami_command))
    app.add_handler(CommandHandler("resume", resume_command))

    # Single-shot buttons
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_LIST}$"), list_servers_button))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_STOP}$"), stop_current_job))

    # Inline Keyboards
    app.add_handler(CallbackQueryHandler(delete_server_callback, pattern=r"^delete_server_"))

    # Conversation: Add Server
    add_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{BTN_ADD}$"), add_start)],
        states={
            ADD_NAME: [MessageHandler(STATE_TEXT_FILTER, add_name)],
            ADD_HOST: [MessageHandler(STATE_TEXT_FILTER, add_host)],
            ADD_USERNAME: [MessageHandler(STATE_TEXT_FILTER, add_username)],
            ADD_PASSWORD: [MessageHandler(STATE_TEXT_FILTER, add_password)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(MENU_BUTTON_FILTER, restart_menu_from_conversation),
        ],
    )
    app.add_handler(add_conv)

    # Conversation: Edit Server (from Inline Button)
    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_server_callback, pattern=r"^edit_server_")],
        states={
            EDIT_NAME: [MessageHandler(STATE_TEXT_FILTER, edit_name)],
            EDIT_HOST: [MessageHandler(STATE_TEXT_FILTER, edit_host)],
            EDIT_USERNAME: [MessageHandler(STATE_TEXT_FILTER, edit_username)],
            EDIT_PASSWORD: [MessageHandler(STATE_TEXT_FILTER, edit_password)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(MENU_BUTTON_FILTER, restart_menu_from_conversation),
        ],
    )
    app.add_handler(edit_conv)

    # Conversation: Test Flow
    test_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{BTN_TEST}$"), test_start)],
        states={
            TEST_TRANSPORT: [MessageHandler(STATE_TEXT_FILTER, test_receive_transport)],
            TEST_TARGET: [MessageHandler(STATE_TEXT_FILTER, test_receive_target)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(MENU_BUTTON_FILTER, restart_menu_from_conversation),
        ],
    )
    app.add_handler(test_conv)

    return app

def main() -> None:
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    settings = load_settings()
    app = build_app(settings)
    
    # Initialize Databases
    app.bot_data["store"].init()
    app.bot_data["job_store"].init()
    
    # Run bot
    app.run_polling()
