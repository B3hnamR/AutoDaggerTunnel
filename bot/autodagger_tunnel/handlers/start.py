from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from ..utils.ui import ICON_OK, ICON_ID, ICON_CANCEL, ICON_SWITCH, MENU, BTN_ADD, BTN_EDIT, BTN_DELETE, BTN_TEST, BTN_LIST
from .servers_handlers import check_access, get_settings, list_servers_button

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return

    settings = get_settings(context)
    mode_line = (
        f"Mode: private ({len(settings.allowed_user_ids)} allowed IDs)"
        if settings.access_mode == "private"
        else "Mode: public"
    )
    text = (
        f"{ICON_OK} AutoDagger Tunnel bot is online.\n"
        f"{mode_line}\n"
        "Use the menu buttons below."
    )
    await update.effective_message.reply_text(text, reply_markup=MENU)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return

    text = (
        "Commands:\n"
        "/start - show menu\n"
        "/whoami - show your telegram user id\n"
        "/cancel - cancel current action\n"
        "/resume [job_id] - resume a stopped/interrupted job\n"
        "\n"
        "Flow:\n"
        "1) Add outbound servers\n"
        "2) Start tunnel test\n"
        "3) Select tunnel mode (quantummux or tun+bip)\n"
        "4) Enter one or more target address:port values\n"
        "5) Bot runs queue and sends final summary\n"
        "6) Use 'Stop Current Job' any time to stop gracefully"
    )
    await update.effective_message.reply_text(text, reply_markup=MENU)


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    await update.effective_message.reply_text(f"{ICON_ID} Your Telegram user id: {user.id}", reply_markup=MENU)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text(f"{ICON_CANCEL} Cancelled.", reply_markup=MENU)
    return ConversationHandler.END

async def restart_menu_from_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await start_command(update, context)
    return ConversationHandler.END
