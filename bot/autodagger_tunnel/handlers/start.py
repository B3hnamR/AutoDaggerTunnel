from __future__ import annotations

from telegram import ReplyKeyboardRemove, Update
from telegram.ext import ContextTypes, ConversationHandler

from ..runtime import get_settings
from ..utils.ui import (
    ICON_CANCEL,
    ICON_ID,
    ICON_OK,
    MENU,
)
from .servers_handlers import check_access


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return

    await update.effective_message.reply_text("Loading dashboard...", reply_markup=ReplyKeyboardRemove())

    settings = get_settings(context)
    mode_line = (
        f"🔒 Private Mode ({len(settings.allowed_user_ids)} users)"
        if settings.access_mode == "private"
        else "🌍 Public Mode"
    )
    text = (
        f"🚀 𝗔𝘂𝘁𝗼𝗗𝗮𝗴𝗴𝗲𝗿 𝗧𝘂𝗻𝗻𝗲𝗹\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{ICON_OK} Status: Online\n"
        f"🛡 {mode_line}\n\n"
        "Welcome! Please select an action from the menu below:"
    )
    await update.effective_message.reply_text(text, reply_markup=MENU)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return

    text = (
        "📚 𝗤𝘂𝗶𝗰𝗸 𝗛𝗲𝗹𝗽 𝗚𝘂𝗶𝗱𝗲\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "𝗖𝗼𝗺𝗺𝗮𝗻𝗱𝘀:\n"
        "🔹 /start - Show Main Menu\n"
        "🔹 /whoami - Show your Telegram ID\n"
        "🔹 /cancel - Cancel current action\n"
        "🔹 /resume [job_id] - Resume an interrupted job\n\n"
        "𝗪𝗼𝗿𝗸𝗳𝗹𝗼𝘄:\n"
        "1️⃣ Add your outbound servers via 'Server Management'.\n"
        "2️⃣ Click 'Start Tunnel Test'.\n"
        "3️⃣ Select tunnel mode (QuantumMux or TUN+BIP).\n"
        "4️⃣ Enter your targets (e.g., 203.0.113.10:443).\n"
        "5️⃣ The bot runs tests automatically and provides a live status dashboard.\n"
    )
    await update.effective_message.reply_text(text, reply_markup=MENU)


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    await update.effective_message.reply_text(
        f"{ICON_ID} 𝗬𝗼𝘂𝗿 𝗧𝗲𝗹𝗲𝗴𝗿𝗮𝗺 𝗜𝗗: `{user.id}`",
        reply_markup=MENU,
        parse_mode="Markdown",
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text(f"{ICON_CANCEL} Action cancelled successfully.", reply_markup=MENU)
    return ConversationHandler.END


async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    await query.edit_message_text("🎛 𝗠𝗮𝗶𝗻 𝗠𝗲𝗻𝘂:\nSelect an option below:", reply_markup=MENU)


async def restart_menu_from_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await start_command(update, context)
    return ConversationHandler.END


async def restart_menu_from_conversation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is not None:
        await query.answer("Current process cancelled.")

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🚫 Current process was cancelled.\nReturning to menu...",
        reply_markup=MENU,
    )
    return ConversationHandler.END
