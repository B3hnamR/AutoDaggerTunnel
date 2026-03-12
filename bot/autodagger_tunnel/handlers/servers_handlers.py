from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from ..db import ServerStore
from ..models import ServerRecord
from ..settings import Settings
from ..utils.ui import (
    ICON_ADD, ICON_WARN, ICON_USER, ICON_LOCK, ICON_OK, ICON_SEARCH, ICON_FAIL,
    ICON_EDIT, ICON_LIST, MENU, build_server_list_keyboard
)
from ..utils.validators import parse_host_input, NAME_RE
from ..ssh_runner import run_ssh_connectivity_check


# State Constants
ADD_NAME, ADD_HOST, ADD_USERNAME, ADD_PASSWORD = range(4)
EDIT_NAME, EDIT_HOST, EDIT_USERNAME, EDIT_PASSWORD = range(4, 8)

def get_store(context: ContextTypes.DEFAULT_TYPE) -> ServerStore:
    return context.application.bot_data["store"]

def get_settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]

async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings = get_settings(context)
    user = update.effective_user
    if user is None:
        return False

    if settings.access_mode == "public":
        return True

    if user.id in settings.allowed_user_ids:
        return True

    await update.effective_message.reply_text(
        f"{ICON_FAIL} Access denied. Your user id: {user.id}",
        reply_markup=MENU,
    )
    return False

# --- LIST SERVERS ---
async def list_servers_text(store: ServerStore) -> str:
    servers = store.list_servers()
    if not servers:
        return f"{ICON_LIST} No servers saved yet."

    lines = [f"{ICON_LIST} Saved servers (Select Edit/Delete below):"]
    return "\n".join(lines)


async def list_servers_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
        
    store = get_store(context)
    servers = store.list_servers()
    
    if not servers:
        await update.effective_message.reply_text(f"{ICON_LIST} No servers saved yet.", reply_markup=MENU)
        return
        
    await update.effective_message.reply_text(f"{ICON_LIST} Saved servers:")
    for server in servers:
        text = f"ID: {server.id} | Name: {server.name}\nHost: {server.host}:{server.port}\nUser: {server.username}"
        await update.effective_message.reply_text(
            text, 
            reply_markup=build_server_list_keyboard(server.id)
        )


# --- ADD SERVER ---
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_access(update, context):
        return ConversationHandler.END
    await update.effective_message.reply_text(f"{ICON_ADD} Send server name (letters, numbers, -, _)")
    return ADD_NAME

async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    if not NAME_RE.match(text):
        await update.effective_message.reply_text(f"{ICON_WARN} Invalid name. Example: server-de-1")
        return ADD_NAME

    context.user_data["add_name"] = text
    await update.effective_message.reply_text("Send host or host:port (example: 1.2.3.4 or 1.2.3.4:22)")
    return ADD_HOST

async def add_host(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    parsed = parse_host_input(update.effective_message.text)
    if parsed is None:
        await update.effective_message.reply_text(f"{ICON_WARN} Invalid host format. Try again.")
        return ADD_HOST

    context.user_data["add_host"] = parsed.host
    context.user_data["add_port"] = parsed.port
    await update.effective_message.reply_text(f"{ICON_USER} Send SSH username (default: root). Send '-' to use root.")
    return ADD_USERNAME

async def add_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.effective_message.text.strip()
    username = "root" if raw in {"", "-"} or raw.lower() == "root" else raw
    context.user_data["add_username"] = username
    await update.effective_message.reply_text(f"{ICON_LOCK} Send SSH password")
    return ADD_PASSWORD

async def add_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    password = update.effective_message.text
    if not password:
        await update.effective_message.reply_text(f"{ICON_WARN} Password cannot be empty.")
        return ADD_PASSWORD

    store = get_store(context)
    name = context.user_data["add_name"]
    host = context.user_data["add_host"]
    port = context.user_data["add_port"]
    username = context.user_data["add_username"]

    try:
        server_id = store.add_server(name=name, host=host, port=port, username=username, password=password)
    except Exception as exc: 
        await update.effective_message.reply_text(f"Failed to save server: {exc}", reply_markup=MENU)
        return ConversationHandler.END

    await update.effective_message.reply_text(
        f"{ICON_OK} Server saved. ID={server_id}, target={host}:{port}\n"
        f"{ICON_SEARCH} Running SSH connectivity check...",
    )

    settings = get_settings(context)
    check_ok, check_detail = await run_ssh_connectivity_check(
        host=host,
        port=port,
        username=username,
        password=password,
        connect_timeout=settings.ssh_connect_timeout,
    )

    if check_ok:
        check_text = f"{ICON_OK} SSH check: SUCCESS (connected)."
    else:
        check_text = (
            f"{ICON_FAIL} SSH check: FAILED.\n"
            f"Reason: {check_detail}\n"
            "Server is still saved (as requested)."
        )

    await update.effective_message.reply_text(check_text, reply_markup=MENU)
    return ConversationHandler.END


# --- EDIT SERVER (Via Inline Button Callback) ---
async def edit_server_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    if not await check_access(update, context):
        return ConversationHandler.END

    server_id_str = query.data.replace("edit_server_", "")
    if not server_id_str.isdigit():
        await query.edit_message_text(f"{ICON_WARN} Invalid ID.")
        return ConversationHandler.END

    server = get_store(context).get_server(int(server_id_str))
    if server is None:
        await query.edit_message_text(f"{ICON_WARN} Server not found.")
        return ConversationHandler.END

    context.user_data["edit_server"] = server
    await query.edit_message_text(f"{ICON_EDIT} Editing [{server.name}] (Leave empty/send '-' to skip each step)")
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"Current name: {server.name}\nSend new name or '-' to keep"
    )
    return EDIT_NAME

async def edit_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    server: ServerRecord = context.user_data["edit_server"]
    raw = update.effective_message.text.strip()

    if raw == "-":
        name = server.name
    else:
        if not NAME_RE.match(raw):
            await update.effective_message.reply_text(f"{ICON_WARN} Invalid name. Try again.")
            return EDIT_NAME
        name = raw

    context.user_data["edit_name"] = name
    await update.effective_message.reply_text(
        f"Current host: {server.host}:{server.port}\nSend new host/host:port or '-' to keep"
    )
    return EDIT_HOST


async def edit_host(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    server: ServerRecord = context.user_data["edit_server"]
    raw = update.effective_message.text.strip()

    if raw == "-":
        host = server.host
        port = server.port
    else:
        parsed = parse_host_input(raw)
        if parsed is None:
            await update.effective_message.reply_text(f"{ICON_WARN} Invalid host format. Try again.")
            return EDIT_HOST
        host = parsed.host
        port = parsed.port

    context.user_data["edit_host"] = host
    context.user_data["edit_port"] = port
    await update.effective_message.reply_text(
        f"Current username: {server.username}\nSend new username or '-' to keep"
    )
    return EDIT_USERNAME


async def edit_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    server: ServerRecord = context.user_data["edit_server"]
    raw = update.effective_message.text.strip()
    username = server.username if raw == "-" else raw
    if username.lower() == "root":
        username = "root"

    if not username:
        await update.effective_message.reply_text(f"{ICON_WARN} Username cannot be empty.")
        return EDIT_USERNAME

    context.user_data["edit_username"] = username
    await update.effective_message.reply_text("Send new password or '-' to keep existing")
    return EDIT_PASSWORD


async def edit_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    store = get_store(context)
    server: ServerRecord = context.user_data["edit_server"]

    raw = update.effective_message.text
    password = None if raw.strip() == "-" else raw
    if password == "":
        await update.effective_message.reply_text(
            f"{ICON_WARN} Password cannot be empty. Use '-' to keep current password."
        )
        return EDIT_PASSWORD

    try:
        ok = store.update_server(
            server.id,
            name=context.user_data["edit_name"],
            host=context.user_data["edit_host"],
            port=context.user_data["edit_port"],
            username=context.user_data["edit_username"],
            password=password,
        )
    except Exception as exc: 
        await update.effective_message.reply_text(f"Edit failed: {exc}", reply_markup=MENU)
        return ConversationHandler.END

    if not ok:
        await update.effective_message.reply_text(f"{ICON_WARN} Server no longer exists.", reply_markup=MENU)
        return ConversationHandler.END

    await update.effective_message.reply_text(f"{ICON_OK} Server updated.", reply_markup=MENU)
    return ConversationHandler.END

# --- DELETE SERVER (Via Inline Button Callback) ---
async def delete_server_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    if not await check_access(update, context):
        return

    server_id_str = query.data.replace("delete_server_", "")
    if not server_id_str.isdigit():
        await query.edit_message_text(f"{ICON_WARN} Invalid ID.")
        return

    ok = get_store(context).delete_server(int(server_id_str))
    if ok:
        await query.edit_message_text(f"{ICON_OK} Server deleted.")
    else:
        await query.edit_message_text(f"{ICON_WARN} Server not found.")
