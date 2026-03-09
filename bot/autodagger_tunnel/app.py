from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Optional, Tuple

import asyncssh
from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .db import ServerStore
from .models import ServerRecord
from .security import load_or_create_fernet
from .settings import Settings, load_settings
from .ssh_runner import DaggerSshTester, ServerTestResult, TestStatus, summarize_results

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("autodagger_tunnel")

ADD_NAME, ADD_HOST, ADD_USERNAME, ADD_PASSWORD = range(4)
EDIT_SELECT_ID, EDIT_NAME, EDIT_HOST, EDIT_USERNAME, EDIT_PASSWORD = range(4, 9)
DELETE_SELECT_ID = 9
TEST_TARGET = 10

BTN_TEST = "🚀 Start Tunnel Test"
BTN_ADD = "➕ Add Server"
BTN_LIST = "📋 List Servers"
BTN_EDIT = "✏️ Edit Server"
BTN_DELETE = "🗑️ Delete Server"
MENU_BUTTONS = (BTN_TEST, BTN_ADD, BTN_LIST, BTN_EDIT, BTN_DELETE)
MENU_BUTTON_PATTERN = "^(" + "|".join(re.escape(item) for item in MENU_BUTTONS) + ")$"
MENU_BUTTON_FILTER = filters.Regex(MENU_BUTTON_PATTERN)
STATE_TEXT_FILTER = filters.TEXT & ~filters.COMMAND & ~MENU_BUTTON_FILTER

MENU = ReplyKeyboardMarkup(
    [[BTN_TEST, BTN_ADD], [BTN_LIST, BTN_EDIT], [BTN_DELETE]],
    resize_keyboard=True,
)

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
TARGET_RE = re.compile(r"^[^\s:]+:\d{1,5}$")


@dataclass
class ParsedHost:
    host: str
    port: int


class LiveLogMessage:
    def __init__(self, app: Application, chat_id: int, title: str) -> None:
        self.app = app
        self.chat_id = chat_id
        self.title = title
        self.message_id: Optional[int] = None
        self.lines: Deque[str] = deque(maxlen=18)
        self.last_flush = 0.0

    async def start(self) -> None:
        msg = await self.app.bot.send_message(
            chat_id=self.chat_id,
            text=f"[{self.title}] Live log started...",
        )
        self.message_id = msg.message_id

    async def push(self, line: str) -> None:
        self.lines.append(self._trim_line(line))
        now = time.monotonic()
        if now - self.last_flush >= 2.0:
            await self.flush()

    async def flush(self, force: bool = False) -> None:
        if self.message_id is None:
            return
        now = time.monotonic()
        if not force and now - self.last_flush < 1.0:
            return

        log_text = "\n".join(self.lines) if self.lines else "(no log lines yet)"
        text = f"[{self.title}] Live logs\n\n{log_text}"
        if len(text) > 3900:
            text = text[-3900:]

        try:
            await self.app.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.message_id,
                text=text,
            )
            self.last_flush = now
        except BadRequest as exc:
            if "Message is not modified" not in str(exc):
                logger.warning("Live log update failed: %s", exc)

    async def close(self, footer: str) -> None:
        self.lines.append(footer)
        await self.flush(force=True)

    @staticmethod
    def _trim_line(line: str) -> str:
        clean = line.replace("\t", " ").strip()
        return clean[:220]


def get_settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


def get_store(context: ContextTypes.DEFAULT_TYPE) -> ServerStore:
    return context.application.bot_data["store"]


def get_tester(context: ContextTypes.DEFAULT_TYPE) -> DaggerSshTester:
    return context.application.bot_data["tester"]


def get_active_chats(context: ContextTypes.DEFAULT_TYPE) -> set[int]:
    return context.application.bot_data["active_chats"]


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
        f"Access denied. Your user id: {user.id}",
        reply_markup=MENU,
    )
    return False


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
        "✅ AutoDagger Tunnel bot is online.\n"
        f"{mode_line}\n"
        "Use the menu buttons below."
    )
    await update.effective_message.reply_text(text, reply_markup=MENU)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return

    text = (
        "📘 Commands:\n"
        "/start - show menu\n"
        "/whoami - show your telegram user id\n"
        "/cancel - cancel current action\n"
        "\n"
        "⚙️ Flow:\n"
        "1) Add your outbound servers\n"
        "2) Press 'Start Tunnel Test'\n"
        "3) Enter target server address:port\n"
        "4) Bot runs checks on all saved servers and streams logs"
    )
    await update.effective_message.reply_text(text, reply_markup=MENU)


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    await update.effective_message.reply_text(f"🆔 Your Telegram user id: {user.id}", reply_markup=MENU)


def parse_host_input(raw: str) -> Optional[ParsedHost]:
    raw = raw.strip()
    if not raw:
        return None

    if ":" in raw:
        host, port_str = raw.rsplit(":", 1)
        host = host.strip()
        port_str = port_str.strip()
        if not host or not port_str.isdigit():
            return None
        port = int(port_str)
        if port < 1 or port > 65535:
            return None
        return ParsedHost(host=host, port=port)

    return ParsedHost(host=raw, port=22)


def validate_target(raw: str) -> bool:
    raw = raw.strip()
    if not TARGET_RE.match(raw):
        return False
    _, port_s = raw.rsplit(":", 1)
    port = int(port_s)
    return 1 <= port <= 65535


def compact_error(exc: Exception) -> str:
    text = str(exc).strip().replace("\n", " | ")
    return text[:280] if text else exc.__class__.__name__


async def run_ssh_connectivity_check(
    host: str,
    port: int,
    username: str,
    password: str,
    connect_timeout: int,
) -> Tuple[bool, str]:
    conn: Optional[asyncssh.SSHClientConnection] = None
    try:
        conn = await asyncssh.connect(
            host,
            port=port,
            username=username,
            password=password,
            known_hosts=None,
            connect_timeout=connect_timeout,
        )
        await conn.run("true", check=True, timeout=max(3, connect_timeout))
        return True, "ssh_connection_successful"
    except Exception as exc:  # noqa: BLE001
        return False, compact_error(exc)
    finally:
        if conn is not None:
            conn.close()
            try:
                await conn.wait_closed()
            except Exception:
                pass


async def list_servers_text(store: ServerStore) -> str:
    servers = store.list_servers()
    if not servers:
        return "📭 No servers saved yet."

    lines = ["📋 Saved servers:"]
    for item in servers:
        lines.append(f"- ID {item.id} | {item.name} | {item.host}:{item.port} | user={item.username}")
    return "\n".join(lines)


async def list_servers_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    text = await list_servers_text(get_store(context))
    await update.effective_message.reply_text(text, reply_markup=MENU)


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_access(update, context):
        return ConversationHandler.END
    await update.effective_message.reply_text("➕ Send server name (letters, numbers, -, _)")
    return ADD_NAME


async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    if not NAME_RE.match(text):
        await update.effective_message.reply_text("⚠️ Invalid name. Example: server-de-1")
        return ADD_NAME

    context.user_data["add_name"] = text
    await update.effective_message.reply_text("🌐 Send host or host:port (example: 1.2.3.4 or 1.2.3.4:22)")
    return ADD_HOST


async def add_host(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    parsed = parse_host_input(update.effective_message.text)
    if parsed is None:
        await update.effective_message.reply_text("⚠️ Invalid host format. Try again.")
        return ADD_HOST

    context.user_data["add_host"] = parsed.host
    context.user_data["add_port"] = parsed.port
    await update.effective_message.reply_text("👤 Send SSH username (default: root). Send '-' to use root.")
    return ADD_USERNAME


async def add_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.effective_message.text.strip()
    username = "root" if raw in {"", "-"} or raw.lower() == "root" else raw
    context.user_data["add_username"] = username
    await update.effective_message.reply_text("🔐 Send SSH password")
    return ADD_PASSWORD


async def add_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    password = update.effective_message.text
    if not password:
        await update.effective_message.reply_text("⚠️ Password cannot be empty.")
        return ADD_PASSWORD

    store = get_store(context)
    name = context.user_data["add_name"]
    host = context.user_data["add_host"]
    port = context.user_data["add_port"]
    username = context.user_data["add_username"]

    try:
        server_id = store.add_server(name=name, host=host, port=port, username=username, password=password)
    except Exception as exc:  # noqa: BLE001
        await update.effective_message.reply_text(f"Failed to save server: {exc}", reply_markup=MENU)
        return ConversationHandler.END

    await update.effective_message.reply_text(
        f"✅ Server saved. ID={server_id}, target={host}:{port}\n🔎 Running SSH connectivity check...",
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
        check_text = "✅ SSH check: SUCCESS (connected)."
    else:
        check_text = (
            "❌ SSH check: FAILED.\n"
            f"Reason: {check_detail}\n"
            "Server is still saved (as requested)."
        )

    await update.effective_message.reply_text(
        check_text,
        reply_markup=MENU,
    )
    return ConversationHandler.END


async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_access(update, context):
        return ConversationHandler.END

    text = await list_servers_text(get_store(context))
    await update.effective_message.reply_text(text)
    await update.effective_message.reply_text("✏️ Send server ID to edit")
    return EDIT_SELECT_ID


async def edit_select_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.effective_message.text.strip()
    if not raw.isdigit():
        await update.effective_message.reply_text("⚠️ Invalid ID. Send a number.")
        return EDIT_SELECT_ID

    server = get_store(context).get_server(int(raw))
    if server is None:
        await update.effective_message.reply_text("⚠️ Server not found. Try another ID.")
        return EDIT_SELECT_ID

    context.user_data["edit_server"] = server
    await update.effective_message.reply_text(
        f"Current name: {server.name}\nSend new name or '-' to keep",
    )
    return EDIT_NAME


async def edit_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    server: ServerRecord = context.user_data["edit_server"]
    raw = update.effective_message.text.strip()

    if raw == "-":
        name = server.name
    else:
        if not NAME_RE.match(raw):
            await update.effective_message.reply_text("⚠️ Invalid name. Try again.")
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
            await update.effective_message.reply_text("⚠️ Invalid host format. Try again.")
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
        await update.effective_message.reply_text("⚠️ Username cannot be empty.")
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
        await update.effective_message.reply_text("⚠️ Password cannot be empty. Use '-' to keep current password.")
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
    except Exception as exc:  # noqa: BLE001
        await update.effective_message.reply_text(f"Edit failed: {exc}", reply_markup=MENU)
        return ConversationHandler.END

    if not ok:
        await update.effective_message.reply_text("⚠️ Server no longer exists.", reply_markup=MENU)
        return ConversationHandler.END

    await update.effective_message.reply_text("✅ Server updated.", reply_markup=MENU)
    return ConversationHandler.END


async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_access(update, context):
        return ConversationHandler.END

    text = await list_servers_text(get_store(context))
    await update.effective_message.reply_text(text)
    await update.effective_message.reply_text("🗑️ Send server ID to delete")
    return DELETE_SELECT_ID


async def delete_pick_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.effective_message.text.strip()
    if not raw.isdigit():
        await update.effective_message.reply_text("⚠️ Invalid ID. Send a number.")
        return DELETE_SELECT_ID

    ok = get_store(context).delete_server(int(raw))
    if ok:
        await update.effective_message.reply_text("✅ Server deleted.", reply_markup=MENU)
    else:
        await update.effective_message.reply_text("⚠️ Server not found.", reply_markup=MENU)
    return ConversationHandler.END


async def test_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_access(update, context):
        return ConversationHandler.END

    active_chats = get_active_chats(context)
    chat_id = update.effective_chat.id
    if chat_id in active_chats:
        await update.effective_message.reply_text(
            "⏳ A test is already running in this chat. Wait until it finishes.",
            reply_markup=MENU,
        )
        return ConversationHandler.END

    if not get_store(context).list_servers():
        await update.effective_message.reply_text("📭 No servers saved yet. Add server first.", reply_markup=MENU)
        return ConversationHandler.END

    await update.effective_message.reply_text("🎯 Send target server address:port (example: 64.176.186.228:443)")
    return TEST_TARGET


async def test_receive_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    target = update.effective_message.text.strip()
    if not validate_target(target):
        await update.effective_message.reply_text("⚠️ Invalid address:port format. Try again.")
        return TEST_TARGET

    chat_id = update.effective_chat.id
    get_active_chats(context).add(chat_id)

    await update.effective_message.reply_text(
        f"🚀 Test started for target {target}. Live logs will be streamed here.",
        reply_markup=MENU,
    )

    context.application.create_task(run_test_batch(context.application, chat_id, target))
    return ConversationHandler.END


async def run_test_batch(app: Application, chat_id: int, target: str) -> None:
    store: ServerStore = app.bot_data["store"]
    settings: Settings = app.bot_data["settings"]
    tester: DaggerSshTester = app.bot_data["tester"]
    active_chats: set[int] = app.bot_data["active_chats"]

    results: list[ServerTestResult] = []
    servers = store.list_servers()

    try:
        for idx, server in enumerate(servers, start=1):
            title = f"{idx}/{len(servers)} {server.name} {server.host}:{server.port}"
            live = LiveLogMessage(app, chat_id, title)
            await live.start()

            async def on_line(line: str) -> None:
                await live.push(line)

            result = await tester.test_server(
                server,
                target_addr=target,
                psk=settings.default_psk,
                on_log_line=on_line,
            )
            results.append(result)

            if result.status == TestStatus.FAILED_PATTERN:
                footer = "❌ Result: FAILED pattern detected, config cleaned up."
            elif result.status == TestStatus.MANUAL_REVIEW:
                footer = "🟡 Result: UNKNOWN pattern, manual review needed."
            elif result.status == TestStatus.SSH_ERROR:
                footer = f"🔴 Result: SSH error ({result.reason})"
            else:
                footer = f"🔴 Result: setup/runtime error ({result.reason})"

            await live.close(footer)

            per_server_report = format_server_report(result)
            await app.bot.send_message(chat_id=chat_id, text=per_server_report)

        final = format_final_report(results)
        await app.bot.send_message(chat_id=chat_id, text=final)

    except Exception as exc:  # noqa: BLE001
        logger.exception("Batch test failed")
        await app.bot.send_message(chat_id=chat_id, text=f"Batch crashed: {exc}")
    finally:
        active_chats.discard(chat_id)


def format_server_report(result: ServerTestResult) -> str:
    status_map = {
        TestStatus.FAILED_PATTERN: "FAILED_PATTERN",
        TestStatus.MANUAL_REVIEW: "MANUAL_REVIEW",
        TestStatus.SSH_ERROR: "SSH_ERROR",
        TestStatus.SETUP_ERROR: "SETUP_ERROR",
    }

    lines = [
        f"🖥️ Server: {result.server_name} ({result.host}:{result.port})",
        f"📌 Status: {status_map[result.status]}",
        f"🧾 Reason: {result.reason}",
        (
            "📈 Counters: "
            f"disconnected={result.analyzer.disconnected_count}, "
            f"reconnect={result.analyzer.reconnect_count}, "
            f"streams_zero={result.analyzer.streams_zero_count}"
        ),
    ]

    if result.log_tail:
        lines.append("🪵 Log tail:")
        lines.extend(f"  {line[:160]}" for line in result.log_tail[-8:])

    return "\n".join(lines)


def format_final_report(results: list[ServerTestResult]) -> str:
    summary = summarize_results(results)

    lines = [
        "✅ Batch completed.",
        (
            "📊 Summary: "
            f"failed_pattern={summary['failed_pattern']} | "
            f"manual_review={summary['manual_review']} | "
            f"ssh_error={summary['ssh_error']} | "
            f"setup_error={summary['setup_error']}"
        ),
        "",
        "🧩 Per server:",
    ]

    for item in results:
        lines.append(f"- {item.server_name} ({item.host}:{item.port}) => {item.status.value} ({item.reason})")

    return "\n".join(lines)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text("🛑 Cancelled.", reply_markup=MENU)
    return ConversationHandler.END


async def cancel_on_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text(
        "↩️ Switched to selected menu action.",
        reply_markup=MENU,
    )
    text = (update.effective_message.text or "").strip()
    if text == BTN_ADD:
        return await add_start(update, context)
    if text == BTN_EDIT:
        return await edit_start(update, context)
    if text == BTN_DELETE:
        return await delete_start(update, context)
    if text == BTN_TEST:
        return await test_start(update, context)
    if text == BTN_LIST:
        await list_servers_button(update, context)
        return ConversationHandler.END
    return ConversationHandler.END


async def restart_menu_from_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await start_command(update, context)
    return ConversationHandler.END


def build_app() -> Application:
    load_dotenv()

    settings = load_settings()
    fernet = load_or_create_fernet(settings.key_file)
    store = ServerStore(settings.db_path, fernet)
    store.init()

    tester = DaggerSshTester(settings)

    app = ApplicationBuilder().token(settings.bot_token).build()

    app.bot_data["settings"] = settings
    app.bot_data["store"] = store
    app.bot_data["tester"] = tester
    app.bot_data["active_chats"] = set()

    main_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(f"^{re.escape(BTN_ADD)}$"), add_start),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_EDIT)}$"), edit_start),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_DELETE)}$"), delete_start),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_TEST)}$"), test_start),
        ],
        states={
            ADD_NAME: [MessageHandler(STATE_TEXT_FILTER, add_name)],
            ADD_HOST: [MessageHandler(STATE_TEXT_FILTER, add_host)],
            ADD_USERNAME: [MessageHandler(STATE_TEXT_FILTER, add_username)],
            ADD_PASSWORD: [MessageHandler(STATE_TEXT_FILTER, add_password)],
            EDIT_SELECT_ID: [MessageHandler(STATE_TEXT_FILTER, edit_select_id)],
            EDIT_NAME: [MessageHandler(STATE_TEXT_FILTER, edit_name)],
            EDIT_HOST: [MessageHandler(STATE_TEXT_FILTER, edit_host)],
            EDIT_USERNAME: [MessageHandler(STATE_TEXT_FILTER, edit_username)],
            EDIT_PASSWORD: [MessageHandler(STATE_TEXT_FILTER, edit_password)],
            DELETE_SELECT_ID: [MessageHandler(STATE_TEXT_FILTER, delete_pick_id)],
            TEST_TARGET: [MessageHandler(STATE_TEXT_FILTER, test_receive_target)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", restart_menu_from_conversation),
            MessageHandler(MENU_BUTTON_FILTER, cancel_on_menu_button),
        ],
        allow_reentry=True,
    )

    app.add_handler(main_conv)

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("whoami", whoami_command))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BTN_LIST)}$"), list_servers_button))

    return app


def main() -> None:
    app = build_app()
    logger.info("AutoDaggerTunnel bot started")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
