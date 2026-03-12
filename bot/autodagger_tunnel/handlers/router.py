from __future__ import annotations

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from ..utils.ui import (
    BTN_ADD,
    BTN_DELETE,
    BTN_EDIT,
    BTN_LIST,
    BTN_STOP,
    BTN_TEST,
    MENU_BUTTON_FILTER,
    STATE_TEXT_FILTER,
)
from .jobs_handlers import (
    TEST_TARGET,
    TEST_TRANSPORT,
    resume_command,
    stop_current_job,
    test_receive_target,
    test_receive_transport,
    test_start,
)
from .servers_handlers import (
    ADD_HOST,
    ADD_NAME,
    ADD_PASSWORD,
    ADD_USERNAME,
    EDIT_HOST,
    EDIT_NAME,
    EDIT_PASSWORD,
    EDIT_USERNAME,
    add_host,
    add_name,
    add_password,
    add_start,
    add_username,
    delete_server_callback,
    edit_host,
    edit_name,
    edit_password,
    edit_server_callback,
    edit_username,
    list_servers_button,
)
from .start import cancel, help_command, restart_menu_from_conversation, start_command, whoami_command


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("whoami", whoami_command))
    app.add_handler(CommandHandler("resume", resume_command))

    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_LIST}$"), list_servers_button))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_EDIT}$"), list_servers_button))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_DELETE}$"), list_servers_button))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_STOP}$"), stop_current_job))

    app.add_handler(CallbackQueryHandler(delete_server_callback, pattern=r"^delete_server_"))

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
