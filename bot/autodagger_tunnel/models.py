from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


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


@dataclass
class JobBatchRecord:
    target: str
    results: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class JobRecord:
    job_id: str
    chat_id: int
    mode: str
    status: str
    targets: list[str]
    pending_targets: list[str]
    completed_batches: list[JobBatchRecord]
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str = ""
