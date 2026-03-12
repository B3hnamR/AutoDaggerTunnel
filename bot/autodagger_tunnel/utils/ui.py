from __future__ import annotations

import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import filters

# --- Icons ---
ICON_OK = "\u2705"
ICON_WARN = "\u26A0\ufe0f"
ICON_FAIL = "\u274C"
ICON_INFO = "\u2139\ufe0f"
ICON_WAIT = "\u23F3"
ICON_ROCKET = "\U0001F680"
ICON_ADD = "\u2795"
ICON_LIST = "\U0001F4CB"
ICON_EDIT = "\u270F\ufe0f"
ICON_DELETE = "\U0001F5D1\ufe0f"
ICON_TARGET = "\U0001F3AF"
ICON_RADAR = "\U0001F6F0\ufe0f"
ICON_CHART = "\U0001F4CA"
ICON_SEARCH = "\U0001F50E"
ICON_LOCK = "\U0001F510"
ICON_USER = "\U0001F464"
ICON_PC = "\U0001F5A5\ufe0f"
ICON_NOTE = "\U0001F9FE"
ICON_SWITCH = "\u21A9\ufe0f"
ICON_CANCEL = "\U0001F6D1"
ICON_ID = "\U0001F194"
ICON_STOP = "\U0001F6D1"
ICON_PLAY = "\u25B6\ufe0f"
ICON_MENU = "\U0001F5C2\ufe0f"
ICON_BACK = "\u2B05\ufe0f"

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

# --- Legacy message buttons (kept for backward compatibility) ---
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
        [InlineKeyboardButton(f"{ICON_MENU} Server Management", callback_data=CB_MENU_SERVERS)],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_server_management_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(f"{ICON_ADD} Add", callback_data=CB_SERVER_ADD),
            InlineKeyboardButton(f"{ICON_LIST} List", callback_data=CB_SERVER_LIST),
        ],
        [
            InlineKeyboardButton(f"{ICON_EDIT} Edit", callback_data=CB_SERVER_EDIT),
            InlineKeyboardButton(f"{ICON_DELETE} Delete", callback_data=CB_SERVER_DELETE),
        ],
        [InlineKeyboardButton(f"{ICON_BACK} Back", callback_data=CB_MENU_MAIN)],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_transport_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("1) quantummux (auto log check)", callback_data=CB_MODE_QUANTUMMUX)],
        [InlineKeyboardButton("2) tun + bip (config only)", callback_data=CB_MODE_TUN_BIP)],
        [InlineKeyboardButton(f"{ICON_BACK} Back", callback_data=CB_MODE_BACK)],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_job_stop_keyboard(job_id: str) -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(f"{ICON_STOP} Stop Current Job", callback_data=f"{CB_JOB_STOP_PREFIX}{job_id}")]]
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
    keyboard.append([InlineKeyboardButton(f"{ICON_BACK} Back", callback_data=CB_MENU_SERVERS)])
    return InlineKeyboardMarkup(keyboard)


def transport_label(mode: str) -> str:
    if mode == "tun_bip":
        return "tun + bip"
    return "quantummux"
