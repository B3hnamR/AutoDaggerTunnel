from __future__ import annotations

import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
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

# --- Main Menu Buttons ---
BTN_TEST = f"{ICON_ROCKET} Start Tunnel Test"
BTN_STOP = f"{ICON_STOP} Stop Current Job"
BTN_ADD = f"{ICON_ADD} Add Server"
BTN_LIST = f"{ICON_LIST} List Servers"
BTN_EDIT = f"{ICON_EDIT} Edit Server"
BTN_DELETE = f"{ICON_DELETE} Delete Server"

MENU_BUTTONS = (BTN_TEST, BTN_STOP, BTN_ADD, BTN_LIST, BTN_EDIT, BTN_DELETE)
MENU_BUTTON_PATTERN = "^(" + "|".join(re.escape(item) for item in MENU_BUTTONS) + ")$"
MENU_BUTTON_FILTER = filters.Regex(MENU_BUTTON_PATTERN)
STATE_TEXT_FILTER = filters.TEXT & ~filters.COMMAND & ~MENU_BUTTON_FILTER

MENU = ReplyKeyboardMarkup(
    [[BTN_TEST, BTN_STOP], [BTN_ADD, BTN_LIST], [BTN_EDIT, BTN_DELETE]],
    resize_keyboard=True,
)

def build_server_list_keyboard(server_id: int) -> InlineKeyboardMarkup:
    """Builds an inline keyboard with Edit and Delete options for a specific server_id."""
    keyboard = [
        [
            InlineKeyboardButton(f"{ICON_EDIT} Edit", callback_data=f"edit_server_{server_id}"),
            InlineKeyboardButton(f"{ICON_DELETE} Delete", callback_data=f"delete_server_{server_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def transport_label(mode: str) -> str:
    if mode == "tun_bip":
        return "tun + bip"
    return "quantummux"
