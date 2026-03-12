from __future__ import annotations

import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import filters

# --- Icons (Modernized) ---
ICON_OK = "✅"
ICON_WARN = "⚠️"
ICON_FAIL = "❌"
ICON_INFO = "ℹ️"
ICON_WAIT = "⏳"
ICON_ROCKET = "🚀"
ICON_ADD = "➕"
ICON_LIST = "📋"
ICON_EDIT = "✏️"
ICON_DELETE = "🗑"
ICON_TARGET = "🎯"
ICON_RADAR = "📡"
ICON_CHART = "📊"
ICON_SEARCH = "🔍"
ICON_LOCK = "🔐"
ICON_USER = "👤"
ICON_PC = "🖥"
ICON_NOTE = "📝"
ICON_SWITCH = "🔄"
ICON_CANCEL = "🚫"
ICON_ID = "🆔"
ICON_STOP = "🛑"
ICON_PLAY = "▶️"
ICON_MENU = "🎛"
ICON_BACK = "🔙"

# --- Callback Data ---
CB_MENU_MAIN = "menu_main"
CB_MENU_TEST = "menu_test"
CB_MENU_SERVERS = "menu_servers"

CB_SERVER_ADD = "srv_add"
CB_SERVER_LIST = "srv_list"
CB_SERVER_EDIT = "srv_edit"
CB_SERVER_DELETE = "srv_delete"
CB_SERVER_BACK = "srv_back"

CB_MODE_QUANTUMMUX = "mode_quantummux"
CB_MODE_TUN_BIP = "mode_tun_bip"
CB_MODE_BACK = "mode_back"

CB_JOB_STOP_PREFIX = "job_stop:"

# --- Legacy message buttons ---
BTN_TEST = f"{ICON_ROCKET} Start Tunnel Test"
BTN_STOP = f"{ICON_STOP} Stop Current Job"
BTN_ADD = f"{ICON_ADD} Add Server"
BTN_LIST = f"{ICON_LIST} List Servers"
BTN_EDIT = f"{ICON_EDIT} Edit Server"
BTN_DELETE = f"{ICON_DELETE} Delete Server"

MENU_BUTTONS = (
    BTN_TEST,
    BTN_STOP,
    BTN_ADD,
    BTN_LIST,
    BTN_EDIT,
    BTN_DELETE,
    "Start Tunnel Test",
    "Stop Current Job",
    "Add Server",
    "List Servers",
    "Edit Server",
    "Delete Server",
)
MENU_BUTTON_PATTERN = "^(" + "|".join(re.escape(item) for item in MENU_BUTTONS) + ")$"
MENU_BUTTON_FILTER = filters.Regex(MENU_BUTTON_PATTERN)
STATE_TEXT_FILTER = filters.TEXT & ~filters.COMMAND


def build_main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(f"{ICON_ROCKET} Start Tunnel Test", callback_data=CB_MENU_TEST)],
        [InlineKeyboardButton(f"{ICON_PC} Server Management", callback_data=CB_MENU_SERVERS)],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_server_management_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(f"{ICON_ADD} Add Server", callback_data=CB_SERVER_ADD),
            InlineKeyboardButton(f"{ICON_LIST} Server List", callback_data=CB_SERVER_LIST),
        ],
        [
            InlineKeyboardButton(f"{ICON_EDIT} Edit", callback_data=CB_SERVER_EDIT),
            InlineKeyboardButton(f"{ICON_DELETE} Delete", callback_data=CB_SERVER_DELETE),
        ],
        [InlineKeyboardButton(f"{ICON_BACK} Back to Main Menu", callback_data=CB_MENU_MAIN)],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_transport_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("⚡️ 1) QuantumMux (Auto Log Check)", callback_data=CB_MODE_QUANTUMMUX)],
        [InlineKeyboardButton("🛡 2) TUN + BIP (Config Only)", callback_data=CB_MODE_TUN_BIP)],
        [InlineKeyboardButton(f"{ICON_BACK} Back", callback_data=CB_MODE_BACK)],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_job_stop_keyboard(job_id: str) -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(f"{ICON_STOP} Abort Current Job", callback_data=f"{CB_JOB_STOP_PREFIX}{job_id}")]]
    return InlineKeyboardMarkup(keyboard)


MENU = build_main_menu_keyboard()


def build_server_list_keyboard(
    server_id: int,
    *,
    include_edit: bool = True,
    include_delete: bool = True,
) -> InlineKeyboardMarkup:
    buttons: list[InlineKeyboardButton] = []
    if include_edit:
        buttons.append(InlineKeyboardButton(f"{ICON_EDIT} Edit", callback_data=f"edit_server_{server_id}"))
    if include_delete:
        buttons.append(InlineKeyboardButton(f"{ICON_DELETE} Delete", callback_data=f"delete_server_{server_id}"))

    keyboard = [buttons] if buttons else []
    keyboard.append([InlineKeyboardButton(f"{ICON_BACK} Back to Servers", callback_data=CB_MENU_SERVERS)])
    return InlineKeyboardMarkup(keyboard)


def transport_label(mode: str) -> str:
    if mode == "tun_bip":
        return "TUN + BIP"
    return "QuantumMux"
