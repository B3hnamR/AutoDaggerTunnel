from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from cryptography.fernet import Fernet

from .models import JobBatchRecord, JobRecord, ServerRecord


class ServerStore:
    def __init__(self, db_path: Path, fernet: Fernet) -> None:
        self.db_path = db_path
        self.fernet = fernet
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL DEFAULT 22,
                    username TEXT NOT NULL,
                    password_enc TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def add_server(self, name: str, host: str, port: int, username: str, password: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        password_enc = self._encrypt(password)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO servers (name, host, port, username, password_enc, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (name, host, port, username, password_enc, now, now),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def list_servers(self) -> List[ServerRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, host, port, username, password_enc, created_at, updated_at FROM servers ORDER BY id"
            ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def get_server(self, server_id: int) -> Optional[ServerRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, name, host, port, username, password_enc, created_at, updated_at FROM servers WHERE id = ?",
                (server_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_model(row)

    def update_server(
        self,
        server_id: int,
        *,
        name: str,
        host: str,
        port: int,
        username: str,
        password: Optional[str] = None,
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            current = conn.execute(
                "SELECT password_enc FROM servers WHERE id = ?",
                (server_id,),
            ).fetchone()
            if current is None:
                return False

            password_enc = current[0] if password is None else self._encrypt(password)
            conn.execute(
                """
                UPDATE servers
                SET name = ?, host = ?, port = ?, username = ?, password_enc = ?, updated_at = ?
                WHERE id = ?
                """,
                (name, host, port, username, password_enc, now, server_id),
            )
            conn.commit()
            return True

    def delete_server(self, server_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM servers WHERE id = ?", (server_id,))
            conn.commit()
            return cursor.rowcount > 0

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def _decrypt(self, value: str) -> str:
        return self.fernet.decrypt(value.encode("utf-8")).decode("utf-8")

    def _row_to_model(self, row: sqlite3.Row) -> ServerRecord:
        return ServerRecord(
            id=int(row["id"]),
            name=row["name"],
            host=row["host"],
            port=int(row["port"]),
            username=row["username"],
            password=self._decrypt(row["password_enc"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


class JobStore:
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_STOPPED = "stopped"
    STATUS_FAILED = "failed"
    STATUS_INTERRUPTED = "interrupted"

    RESUMABLE_STATUSES = {STATUS_STOPPED, STATUS_FAILED, STATUS_INTERRUPTED}

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    targets_json TEXT NOT NULL,
                    pending_targets_json TEXT NOT NULL,
                    completed_batches_json TEXT NOT NULL,
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                )
                """
            )
            conn.commit()

    def create_job(self, *, chat_id: int, mode: str, targets: list[str]) -> JobRecord:
        now = datetime.now(timezone.utc).isoformat()
        job_id = self._new_job_id()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, chat_id, mode, status, targets_json, pending_targets_json,
                    completed_batches_json, error_message, created_at, updated_at, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    chat_id,
                    mode,
                    self.STATUS_RUNNING,
                    self._dump_json(targets),
                    self._dump_json(targets),
                    self._dump_json([]),
                    "",
                    now,
                    now,
                    now,
                    None,
                ),
            )
            conn.commit()
        return self.get_job(job_id)  # type: ignore[return-value]

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def get_latest_resumable_job(self, chat_id: int) -> Optional[JobRecord]:
        with self._connect() as conn:
            placeholders = ",".join("?" for _ in self.RESUMABLE_STATUSES)
            row = conn.execute(
                f"""
                SELECT * FROM jobs
                WHERE chat_id = ? AND status IN ({placeholders})
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (chat_id, *self.RESUMABLE_STATUSES),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def set_running(self, job_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, updated_at = ?, started_at = COALESCE(started_at, ?), finished_at = NULL, error_message = ''
                WHERE job_id = ?
                """,
                (self.STATUS_RUNNING, now, now, job_id),
            )
            conn.commit()

    def append_completed_target(self, job_id: str, target: str, results: list[dict]) -> Optional[JobRecord]:
        job = self.get_job(job_id)
        if job is None:
            return None

        pending = [item for item in job.pending_targets if item != target]
        completed = list(job.completed_batches)
        completed.append(JobBatchRecord(target=target, results=results))
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET pending_targets_json = ?, completed_batches_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    self._dump_json(pending),
                    self._dump_json([self._batch_to_dict(item) for item in completed]),
                    now,
                    job_id,
                ),
            )
            conn.commit()
        return self.get_job(job_id)

    def set_completed(self, job_id: str) -> None:
        self._set_terminal_status(job_id, self.STATUS_COMPLETED, "")

    def set_stopped(self, job_id: str, message: str = "") -> None:
        self._set_terminal_status(job_id, self.STATUS_STOPPED, message)

    def set_failed(self, job_id: str, message: str) -> None:
        self._set_terminal_status(job_id, self.STATUS_FAILED, message)

    def mark_running_as_interrupted(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = ?, updated_at = ?, error_message = ?
                WHERE status = ?
                """,
                (
                    self.STATUS_INTERRUPTED,
                    now,
                    "interrupted_by_process_restart",
                    self.STATUS_RUNNING,
                ),
            )
            conn.commit()
            return cursor.rowcount

    def _set_terminal_status(self, job_id: str, status: str, message: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, error_message = ?, updated_at = ?, finished_at = ?
                WHERE job_id = ?
                """,
                (status, message, now, now, job_id),
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _new_job_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def _dump_json(self, value: object) -> str:
        return json.dumps(value, ensure_ascii=True)

    def _load_json(self, value: str, *, default: object) -> object:
        try:
            return json.loads(value)
        except Exception:
            return default

    def _row_to_job(self, row: sqlite3.Row) -> JobRecord:
        targets = self._safe_str_list(self._load_json(row["targets_json"], default=[]))
        pending_targets = self._safe_str_list(self._load_json(row["pending_targets_json"], default=[]))
        batches_raw = self._load_json(row["completed_batches_json"], default=[])
        batches: list[JobBatchRecord] = []
        completed_dicts = self._load_json(row["completed_batches_json"], default=[])
        completed: list[JobBatchRecord] = []
        if isinstance(completed_dicts, list):
            for d in completed_dicts:
                if isinstance(d, dict):
                    completed.append(JobBatchRecord(target=d.get("target", ""), results=d.get("results", [])))
            
        return JobRecord(
            job_id=row["job_id"],
            chat_id=int(row["chat_id"]),
            mode=row["mode"],
            status=row["status"],
            targets=self._safe_str_list(self._load_json(row["targets_json"], default=[])),
            pending_targets=self._safe_str_list(self._load_json(row["pending_targets_json"], default=[])),
            completed_batches=completed,
            error_message=row["error_message"] or "",
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
        )

    def save_server_result(self, job_id: str, target: str, result_data: dict) -> None:
        """Saves a single server's result incrementally."""
        job = self.get_job(job_id)
        if not job:
            return
            
        completed = list(job.completed_batches)
        target_batch = next((b for b in completed if b.target == target), None)
        
        if not target_batch:
            target_batch = JobBatchRecord(target=target, results=[])
            completed.append(target_batch)
            
        target_batch.results.append(result_data)
        
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET completed_batches_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (self._dump_json([self._batch_to_dict(b) for b in completed]), now, job_id)
            )
            conn.commit()

    def mark_target_done(self, job_id: str, target: str) -> None:
        """Removes the target from pending_targets_json."""
        job = self.get_job(job_id)
        if not job:
            return
            
        pending = [t for t in job.pending_targets if t != target]
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET pending_targets_json = ?, updated_at = ? WHERE job_id = ?",
                (self._dump_json(pending), now, job_id)
            )
            conn.commit()

    def update_job_status(self, job_id: str, status: str) -> None:
        """Updates the status and updates finished_at appropriately."""
        now = datetime.now(timezone.utc).isoformat()
        finished = now if status in self.RESUMABLE_STATUSES or status == self.STATUS_COMPLETED else None
        
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ?, finished_at = COALESCE(finished_at, ?) WHERE job_id = ?",
                (status, now, finished, job_id)
            )
            conn.commit()

    def _safe_str_list(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        output: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                output.append(text)
        return output

    def _batch_to_dict(self, batch: JobBatchRecord) -> dict:
        return {
            "target": batch.target,
            "results": batch.results,
        }
