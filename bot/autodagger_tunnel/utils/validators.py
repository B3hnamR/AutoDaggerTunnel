from __future__ import annotations

import re
from typing import Optional
from dataclasses import dataclass

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
TARGET_RE = re.compile(r"^[^\s:]+:\d{1,5}$")


@dataclass
class ParsedHost:
    host: str
    port: int


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


def parse_targets_input(raw: str) -> tuple[list[str], list[str]]:
    tokens = [item.strip() for item in re.split(r"[,;\s]+", raw.strip()) if item.strip()]

    unique_targets: list[str] = []
    invalid_targets: list[str] = []
    seen: set[str] = set()

    for token in tokens:
        if token in seen:
            continue
        seen.add(token)

        if validate_target(token):
            unique_targets.append(token)
        else:
            invalid_targets.append(token)

    return unique_targets, invalid_targets


def parse_transport_choice(raw: str) -> Optional[str]:
    text = raw.strip().lower()
    if text in {"1", "10", "quantummux", "quantum", "qm", "q"}:
        return "quantummux"
    if text in {"2", "9", "tun+bip", "tun + bip", "tun-bip", "tun", "bip"}:
        return "tun_bip"
    if text in {"3", "11", "ghostmux", "ghost", "gm", "g"}:
        return "ghostmux"
    return None


def compact_error(exc: Exception) -> str:
    text = str(exc).strip().replace("\n", " | ")
    return text[:280] if text else exc.__class__.__name__

