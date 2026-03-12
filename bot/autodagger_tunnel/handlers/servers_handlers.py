from __future__ import annotations

from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from ..models import ServerRecord
from ..runtime import get_settings, get_store
from ..ssh_runner import run_ssh_connectivity_check
from ..utils.ui import (
    CB_MENU_SERVERS,
    CB_SERVER_CHECK_PREFIX,
    CB_SERVER_PAGE_PREFIX,
    ICON_ADD,
    ICON_EDIT,
    ICON_FAIL,
    ICON_LOCK,
    ICON_OK,
    ICON_SEARCH,
    ICON_USER,
    ICON_WARN,
    MENU,
    build_server_carousel_keyboard,
    build_server_management_keyboard,
)
from ..utils.validators import NAME_RE, parse_host_input


ADD_NAME, ADD_HOST, ADD_USERNAME, ADD_PASSWORD = range(4)
EDIT_NAME, EDIT_HOST, EDIT_USERNAME, EDIT_PASSWORD = range(4, 8)


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


async def _render_server_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
    index: int,
    edit_message: bool = False,
    status_text: str = "",
) -> None:
    store = get_store(context)
    servers = store.list_servers()

    if not servers:
        text = f"{ICON_WARN} No servers saved yet. Please add one first."
        reply_markup = build_server_management_keyboard()
        if edit_message and update.callback_query:
            await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup)
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup)
        return

    index = index % len(servers)
    server = servers[index]

    title = "📋 <b>𝗦𝗮𝘃𝗲𝗱 𝗦𝗲𝗿𝘃𝗲𝗿𝘀</b>"
    if action == "edit":
        title = "✏️ <b>𝗦𝗲𝗹𝗲𝗰𝘁 𝘁𝗼 𝗘𝗱𝗶𝘁</b>"
    elif action == "del":
        title = "🗑 <b>𝗦𝗲𝗹𝗲𝗰𝘁 𝘁𝗼 𝗗𝗲𝗹𝗲𝘁𝗲</b>"

    name_escaped = escape(server.name)
    host_escaped = escape(f"{server.host}:{server.port}")
    user_escaped = escape(server.username)

    text = (
        f"{title}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🖥 <b>𝗦𝗲𝗿𝘃𝗲𝗿 𝗜𝗗:</b> {server.id}\n"
        f"🏷 <b>𝗡𝗮𝗺𝗲:</b> <code>{name_escaped}</code>\n"
        f"🌐 <b>𝗛𝗼𝘀𝘁:</b> <code>{host_escaped}</code>\n"
        f"👤 <b>𝗨𝘀𝗲𝗿:</b> <code>{user_escaped}</code>"
    )

    if status_text:
        text += f"\n━━━━━━━━━━━━━━━━\n{status_text}"

    reply_markup = build_server_carousel_keyboard(server.id, index, len(servers), action)

    if edit_message and update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
        )


async def list_servers_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    if update.callback_query:
        await update.callback_query.answer()
    await _render_server_page(update, context, action="all", index=0, edit_message=bool(update.callback_query))


async def list_servers_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    if update.callback_query:
        await update.callback_query.answer()
    await _render_server_page(update, context, action="edit", index=0, edit_message=bool(update.callback_query))


async def list_servers_for_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    if update.callback_query:
        await update.callback_query.answer()
    await _render_server_page(update, context, action="del", index=0, edit_message=bool(update.callback_query))


async def server_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not await check_access(update, context):
        return

    data = query.data.replace(CB_SERVER_PAGE_PREFIX, "")
    try:
        action, index_str = data.split("_", 1)
        index = int(index_str)
    except Exception:
        await query.answer("Invalid page data", show_alert=True)
        return

    await _render_server_page(update, context, action=action, index=index, edit_message=True)


async def ignore_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.callback_query:
        await update.callback_query.answer()


async def check_server_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    if not await check_access(update, context):
        return

    data = query.data.replace(CB_SERVER_CHECK_PREFIX, "")
    try:
        server_id_str, index_str = data.split("_", 1)
        server_id = int(server_id_str)
        index = int(index_str)
    except Exception:
        await query.answer("Invalid check request", show_alert=True)
        return

    server = get_store(context).get_server(server_id)
    if server is None:
        await query.answer("❌ Server not found!", show_alert=True)
        return

    await query.answer("Testing SSH... Please wait ⏳")
    await _render_server_page(
        update,
        context,
        action="all",
        index=index,
        edit_message=True,
        status_text="🔄 <i>Testing SSH connection, please wait...</i>",
    )

    settings = get_settings(context)
    check_ok, check_detail = await run_ssh_connectivity_check(
        host=server.host,
        port=server.port,
        username=server.username,
        password=server.password,
        connect_timeout=settings.ssh_connect_timeout,
        max_retries=1,
    )

    if check_ok:
        final_status = "🚦 <b>𝗦𝘁𝗮𝘁𝘂𝘀:</b> ✅ Online (Connection Successful)"
    else:
        final_status = f"🚦 <b>𝗦𝘁𝗮𝘁𝘂𝘀:</b> ❌ Offline\n⚠️ <b>Reason:</b> <code>{escape(check_detail)}</code>"

    await _render_server_page(
        update,
        context,
        action="all",
        index=index,
        edit_message=True,
        status_text=final_status,
    )


async def server_management_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return

    query = update.callback_query
    if query is not None:
        await query.answer()
        await query.edit_message_text("Server management:", reply_markup=build_server_management_keyboard())
        return

    await update.effective_message.reply_text("Server management:", reply_markup=build_server_management_keyboard())


async def add_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is not None:
        await query.answer()
    return await add_start(update, context)


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
        await update.effective_message.reply_text(
            f"Failed to save server: {exc}",
            reply_markup=build_server_management_keyboard(),
        )
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
        max_retries=settings.ssh_max_retries,
        retry_backoff_seconds=settings.ssh_retry_backoff_seconds,
        keepalive_interval=settings.ssh_keepalive_interval,
        keepalive_count_max=settings.ssh_keepalive_count_max,
    )

    if check_ok:
        check_text = f"{ICON_OK} SSH check: SUCCESS (connected)."
    else:
        check_text = (
            f"{ICON_FAIL} SSH check: FAILED.\n"
            f"Reason: {check_detail}\n"
            "Server is still saved (as requested)."
        )

    await update.effective_message.reply_text(check_text, reply_markup=build_server_management_keyboard())
    return ConversationHandler.END


async def edit_server_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if not await check_access(update, context):
        return ConversationHandler.END

    server_id_str = query.data.replace("edit_server_", "")
    if not server_id_str.isdigit():
        await query.edit_message_text(f"{ICON_WARN} Invalid ID.", reply_markup=build_server_management_keyboard())
        return ConversationHandler.END

    server = get_store(context).get_server(int(server_id_str))
    if server is None:
        await query.edit_message_text(f"{ICON_WARN} Server not found.", reply_markup=build_server_management_keyboard())
        return ConversationHandler.END

    context.user_data["edit_server"] = server
    await query.edit_message_text(f"{ICON_EDIT} Editing [{server.name}] (Leave empty/send '-' to skip each step)")
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"Current name: {server.name}\nSend new name or '-' to keep",
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
        await update.effective_message.reply_text(
            f"Edit failed: {exc}",
            reply_markup=build_server_management_keyboard(),
        )
        return ConversationHandler.END

    if not ok:
        await update.effective_message.reply_text(
            f"{ICON_WARN} Server no longer exists.",
            reply_markup=build_server_management_keyboard(),
        )
        return ConversationHandler.END

    await update.effective_message.reply_text(f"{ICON_OK} Server updated.", reply_markup=build_server_management_keyboard())
    return ConversationHandler.END


async def delete_server_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not await check_access(update, context):
        return

    server_id_str = query.data.replace("delete_server_", "").replace("confirm_delete_", "")
    if not server_id_str.isdigit():
        await query.edit_message_text(f"{ICON_WARN} Invalid ID.")
        return

    keyboard = [
        [
            InlineKeyboardButton("❌ Yes, Delete it!", callback_data=f"execute_delete_{server_id_str}"),
            InlineKeyboardButton("🔙 Cancel", callback_data=CB_MENU_SERVERS),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"⚠️ 𝗔𝗿𝗲 𝘆𝗼𝘂 𝘀𝘂𝗿𝗲?\n━━━━━━━━━━━━━━━━\nDo you really want to delete Server ID: {server_id_str}?\nThis action cannot be undone.",
        reply_markup=reply_markup,
    )


async def execute_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not await check_access(update, context):
        return

    server_id_str = query.data.replace("execute_delete_", "")
    if not server_id_str.isdigit():
        await query.edit_message_text(f"{ICON_WARN} Invalid ID.")
        return

    ok = get_store(context).delete_server(int(server_id_str))
    if ok:
        await query.edit_message_text(
            f"{ICON_OK} 𝗦𝗲𝗿𝘃𝗲𝗿 𝗱𝗲𝗹𝗲𝘁𝗲𝗱 𝘀𝘂𝗰𝗰𝗲𝘀𝘀𝗳𝘂𝗹𝗹𝘆.",
            reply_markup=build_server_management_keyboard(),
        )
    else:
        await query.edit_message_text(
            f"{ICON_WARN} Server not found or already deleted.",
            reply_markup=build_server_management_keyboard(),
        )
