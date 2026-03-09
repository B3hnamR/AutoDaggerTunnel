from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ServerRecord:
    id: int
    name: str
    host: str
    port: int
    username: str
    password: str
    created_at: datetime
    updated_at: datetime
