from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from cryptography.fernet import Fernet

from .models import ServerRecord


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
