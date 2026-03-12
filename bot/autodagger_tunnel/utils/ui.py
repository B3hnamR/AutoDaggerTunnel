from __future__ import annotations

import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import filters

# --- Icons (Modernized) ---
ICON_OK = "\u2705"
ICON_WARN = "\u26a0\ufe0f"
ICON_FAIL = "\u274c"
ICON_INFO = "\u2139\ufe0f"
ICON_WAIT = "\u23f3"
ICON_ROCKET = "\U0001f680"
ICON_ADD = "\u2795"
ICON_LIST = "\U0001f4cb"
ICON_EDIT = "\u270f\ufe0f"
ICON_DELETE = "\U0001f5d1"
ICON_TARGET = "\U0001f3af"
ICON_RADAR = "\U0001f4e1"
ICON_CHART = "\U0001f4ca"
ICON_SEARCH = "\U0001f50d"
ICON_LOCK = "\U0001f510"
ICON_USER = "\U0001f464"
ICON_PC = "\U0001f5a5"
ICON_NOTE = "\U0001f4dd"
ICON_SWITCH = "\U0001f504"
ICON_CANCEL = "\U0001f6ab"
ICON_ID = "\U0001f194"
ICON_STOP = "\U0001f6d1"
ICON_PLAY = "\u25b6\ufe0f"
ICON_MENU = "\U0001f39b"
ICON_BACK = "\U0001f519"

# --- Callback Data ---
CB_MENU_MAIN = "menu_main"
CB_MENU_TEST = "menu_test"
CB_MENU_SERVERS = "menu_servers"

CB_SERVER_ADD = "srv_add"
CB_SERVER_LIST = "srv_list"
CB_SERVER_EDIT = "srv_edit"
CB_SERVER_DELETE = "srv_delete"
CB_SERVER_BACK = "srv_back"
CB_SERVER_PAGE_PREFIX = "srv_page_"
CB_SERVER_CHECK_PREFIX = "srv_chk_"

CB_MODE_QUANTUMMUX = "mode_quantummux"
CB_MODE_TUN_BIP = "mode_tun_bip"
CB_MODE_BACK = "mode_back"

CB_JOB_STOP_PREFIX = "job_stop:"
CB_TEST_SERVER_ALL = "test_srv_all"
CB_TEST_SERVER_PREFIX = "test_srv_item_"

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


def build_test_server_selection_keyboard(servers: list) -> InlineKeyboardMarkup:
    keyboard = []
    keyboard.append([InlineKeyboardButton("🌐 𝗥𝘂𝗻 𝗼𝗻 𝗔𝗟𝗟 𝗦𝗲𝗿𝘃𝗲𝗿𝘀", callback_data=CB_TEST_SERVER_ALL)])

    for srv in servers:
        btn_text = f"🖥 {srv.name} ({srv.host})"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"{CB_TEST_SERVER_PREFIX}{srv.id}")])

    keyboard.append([InlineKeyboardButton(f"{ICON_CANCEL} Cancel", callback_data=CB_MODE_BACK)])
    return InlineKeyboardMarkup(keyboard)


MENU = build_main_menu_keyboard()


def build_server_carousel_keyboard(
    server_id: int,
    current_index: int,
    total: int,
    action: str = "all",
) -> InlineKeyboardMarkup:
    keyboard = []

    action_buttons = []
    if action in ("all", "edit"):
        action_buttons.append(InlineKeyboardButton(f"{ICON_EDIT} Edit", callback_data=f"edit_server_{server_id}"))
    if action in ("all", "del"):
        action_buttons.append(InlineKeyboardButton(f"{ICON_DELETE} Delete", callback_data=f"delete_server_{server_id}"))
    if action_buttons:
        keyboard.append(action_buttons)

    if action == "all":
        keyboard.append([InlineKeyboardButton("🔄 Test SSH Connection", callback_data=f"{CB_SERVER_CHECK_PREFIX}{server_id}_{current_index}")])

    if total > 1:
        prev_idx = (current_index - 1) % total
        next_idx = (current_index + 1) % total
        nav_buttons = [
            InlineKeyboardButton("⬅️ Prev", callback_data=f"{CB_SERVER_PAGE_PREFIX}{action}_{prev_idx}"),
            InlineKeyboardButton(f"📄 {current_index + 1} / {total}", callback_data="ignore"),
            InlineKeyboardButton("Next ➡️", callback_data=f"{CB_SERVER_PAGE_PREFIX}{action}_{next_idx}"),
        ]
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton(f"{ICON_BACK} Back to Servers", callback_data=CB_MENU_SERVERS)])
    return InlineKeyboardMarkup(keyboard)


def transport_label(mode: str) -> str:
    base_mode = mode.split(":")[0] if ":" in mode else mode
    if base_mode == "tun_bip":
        return "TUN + BIP"
    return "QuantumMux"
