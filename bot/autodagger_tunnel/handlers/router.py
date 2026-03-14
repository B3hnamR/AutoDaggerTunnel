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
    CB_JOB_STOP_PREFIX,
    CB_MENU_MAIN,
    CB_MENU_SERVERS,
    CB_MENU_TEST,
    CB_MODE_BACK,
    CB_MODE_GHOSTMUX,
    CB_MODE_QUANTUMMUX,
    CB_MODE_TUN_BIP,
    CB_SERVER_ADD,
    CB_SERVER_CHECK_PREFIX,
    CB_SERVER_DELETE,
    CB_SERVER_EDIT,
    CB_SERVER_LIST,
    CB_SERVER_PAGE_PREFIX,
    MENU_BUTTON_FILTER,
    STATE_TEXT_FILTER,
)
from .jobs_handlers import (
    TEST_TARGET,
    TEST_SERVER_SELECT,
    TEST_TRANSPORT,
    resume_command,
    stop_current_job,
    test_receive_server_selection,
    test_start_callback,
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
    add_start_callback,
    add_username,
    check_server_callback,
    delete_server_callback,
    execute_delete_callback,
    edit_host,
    edit_name,
    edit_password,
    edit_server_callback,
    edit_username,
    ignore_callback,
    list_servers_for_delete,
    list_servers_for_edit,
    list_servers_button,
    server_page_callback,
    server_management_menu,
)
from .start import (
    cancel,
    help_command,
    main_menu_callback,
    restart_menu_from_conversation,
    restart_menu_from_conversation_callback,
    start_command,
    whoami_command,
)


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("whoami", whoami_command))
    app.add_handler(CommandHandler("resume", resume_command))

    app.add_handler(CallbackQueryHandler(main_menu_callback, pattern=rf"^{CB_MENU_MAIN}$"), group=1)
    app.add_handler(CallbackQueryHandler(server_management_menu, pattern=rf"^{CB_MENU_SERVERS}$"), group=1)
    app.add_handler(CallbackQueryHandler(list_servers_button, pattern=rf"^{CB_SERVER_LIST}$"), group=1)
    app.add_handler(CallbackQueryHandler(list_servers_for_edit, pattern=rf"^{CB_SERVER_EDIT}$"), group=1)
    app.add_handler(CallbackQueryHandler(list_servers_for_delete, pattern=rf"^{CB_SERVER_DELETE}$"), group=1)
    app.add_handler(CallbackQueryHandler(check_server_callback, pattern=rf"^{CB_SERVER_CHECK_PREFIX}"), group=1)
    app.add_handler(CallbackQueryHandler(server_page_callback, pattern=rf"^{CB_SERVER_PAGE_PREFIX}"), group=1)
    app.add_handler(CallbackQueryHandler(ignore_callback, pattern=r"^ignore$"), group=1)
    app.add_handler(CallbackQueryHandler(stop_current_job, pattern=rf"^{CB_JOB_STOP_PREFIX}"), group=1)

    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_LIST}$"), list_servers_button))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_EDIT}$"), list_servers_for_edit))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_DELETE}$"), list_servers_for_delete))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_STOP}$"), stop_current_job))

    app.add_handler(CallbackQueryHandler(delete_server_callback, pattern=r"^delete_server_"))
    app.add_handler(CallbackQueryHandler(execute_delete_callback, pattern=r"^execute_delete_"))

    add_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(f"^{BTN_ADD}$"), add_start),
            CallbackQueryHandler(add_start_callback, pattern=rf"^{CB_SERVER_ADD}$"),
        ],
        states={
            ADD_NAME: [MessageHandler(STATE_TEXT_FILTER, add_name)],
            ADD_HOST: [MessageHandler(STATE_TEXT_FILTER, add_host)],
            ADD_USERNAME: [MessageHandler(STATE_TEXT_FILTER, add_username)],
            ADD_PASSWORD: [MessageHandler(STATE_TEXT_FILTER, add_password)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(MENU_BUTTON_FILTER, restart_menu_from_conversation),
            CallbackQueryHandler(restart_menu_from_conversation_callback, pattern=r"^(menu_|srv_)"),
        ],
        allow_reentry=True,
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
            CallbackQueryHandler(restart_menu_from_conversation_callback, pattern=r"^(menu_|srv_)"),
        ],
        allow_reentry=True,
    )
    app.add_handler(edit_conv)

    test_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(f"^{BTN_TEST}$"), test_start),
            CallbackQueryHandler(test_start_callback, pattern=rf"^{CB_MENU_TEST}$"),
        ],
        states={
            TEST_TRANSPORT: [
                CallbackQueryHandler(
                    test_receive_transport,
                    pattern=rf"^({CB_MODE_QUANTUMMUX}|{CB_MODE_TUN_BIP}|{CB_MODE_GHOSTMUX}|{CB_MODE_BACK})$",
                ),
                MessageHandler(STATE_TEXT_FILTER, test_receive_transport),
            ],
            TEST_SERVER_SELECT: [
                CallbackQueryHandler(test_receive_server_selection, pattern=r"^(test_srv_.*)$"),
            ],
            TEST_TARGET: [MessageHandler(STATE_TEXT_FILTER, test_receive_target)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(MENU_BUTTON_FILTER, restart_menu_from_conversation),
            CallbackQueryHandler(restart_menu_from_conversation_callback, pattern=r"^(menu_|srv_|mode_)"),
        ],
        allow_reentry=True,
    )
    app.add_handler(test_conv)
