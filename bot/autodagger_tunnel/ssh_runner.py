from __future__ import annotations

import asyncio
import shlex
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Dict, List, Optional

import asyncssh

from .log_analyzer import AnalyzerSnapshot, DaggerLogAnalyzer
from .models import ServerRecord
from .settings import Settings
from .templates import render_client_yaml, render_service_unit

OnLogLine = Callable[[str], Awaitable[None]]


class TestStatus(str, Enum):
    SUCCESS = "success"
    FAILED_PATTERN = "failed_pattern"
    MANUAL_REVIEW = "manual_review"
    SSH_ERROR = "ssh_error"
    SETUP_ERROR = "setup_error"


@dataclass
class ServerTestResult:
    server_id: int
    server_name: str
    host: str
    port: int
    target_addr: str
    status: TestStatus
    reason: str
    analyzer: AnalyzerSnapshot = field(default_factory=lambda: AnalyzerSnapshot(0, 0, 0, 0, ""))
    log_tail: List[str] = field(default_factory=list)


class DaggerSshTester:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def test_server(
        self,
        server: ServerRecord,
        target_addr: str,
        psk: str,
        *,
        on_log_line: Optional[OnLogLine] = None,
    ) -> ServerTestResult:
        conn: Optional[asyncssh.SSHClientConnection] = None

        try:
            conn = await asyncssh.connect(
                server.host,
                port=server.port,
                username=server.username,
                password=server.password,
                known_hosts=None,
                connect_timeout=self.settings.ssh_connect_timeout,
            )
        except Exception as exc:  # noqa: BLE001
            return ServerTestResult(
                server_id=server.id,
                server_name=server.name,
                host=server.host,
                port=server.port,
                target_addr=target_addr,
                status=TestStatus.SSH_ERROR,
                reason=f"ssh_connect_failed: {self._compact_error(exc)}",
            )

        try:
            await self._require_root(conn)
            await self._install_dagger_binary(conn)
            await self._write_remote_file(conn, "/etc/DaggerConnect/client.yaml", render_client_yaml(target_addr, psk))
            await self._write_remote_file(
                conn,
                "/etc/systemd/system/DaggerConnect-client.service",
                render_service_unit(),
            )
            await self._run(conn, "systemctl daemon-reload")
            await self._run(conn, "systemctl restart DaggerConnect-client")
            await self._run(conn, "systemctl is-active DaggerConnect-client")

            analyzer, log_tail = await self._stream_logs(
                conn,
                self.settings.test_window_seconds,
                on_log_line=on_log_line,
            )

            snapshot = analyzer.snapshot()
            if analyzer.is_failure():
                await self._cleanup_failed_client(conn)
                return ServerTestResult(
                    server_id=server.id,
                    server_name=server.name,
                    host=server.host,
                    port=server.port,
                    target_addr=target_addr,
                    status=TestStatus.FAILED_PATTERN,
                    reason=f"known_failure_pattern: {snapshot.failure_reason}",
                    analyzer=snapshot,
                    log_tail=log_tail,
                )

            if snapshot.connected_count > 0:
                return ServerTestResult(
                    server_id=server.id,
                    server_name=server.name,
                    host=server.host,
                    port=server.port,
                    target_addr=target_addr,
                    status=TestStatus.SUCCESS,
                    reason="connection_detected_without_known_failure_pattern",
                    analyzer=snapshot,
                    log_tail=log_tail,
                )

            return ServerTestResult(
                server_id=server.id,
                server_name=server.name,
                host=server.host,
                port=server.port,
                target_addr=target_addr,
                status=TestStatus.MANUAL_REVIEW,
                reason="no_known_failure_pattern_but_no_connection_signal",
                analyzer=snapshot,
                log_tail=log_tail,
            )

        except Exception as exc:  # noqa: BLE001
            if conn is not None:
                await self._safe_cleanup_on_error(conn)

            return ServerTestResult(
                server_id=server.id,
                server_name=server.name,
                host=server.host,
                port=server.port,
                target_addr=target_addr,
                status=TestStatus.SETUP_ERROR,
                reason=f"setup_or_runtime_error: {self._compact_error(exc)}",
            )
        finally:
            if conn is not None:
                conn.close()
                try:
                    await conn.wait_closed()
                except Exception:
                    pass

    async def _stream_logs(
        self,
        conn: asyncssh.SSHClientConnection,
        seconds: int,
        *,
        on_log_line: Optional[OnLogLine],
    ) -> tuple[DaggerLogAnalyzer, List[str]]:
        analyzer = DaggerLogAnalyzer()
        log_tail: deque[str] = deque(maxlen=40)
        proc = await conn.create_process("journalctl -u DaggerConnect-client -n 0 -f --no-pager -o cat")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds

        while loop.time() < deadline:
            timeout = min(1.2, max(0.05, deadline - loop.time()))
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
            except asyncio.TimeoutError:
                continue

            if not line:
                continue

            clean_line = line.rstrip("\r\n")
            if not clean_line:
                continue

            analyzer.ingest(clean_line)
            log_tail.append(clean_line)

            if on_log_line is not None:
                await on_log_line(clean_line)

            if analyzer.is_failure():
                break

        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

        return analyzer, list(log_tail)

    async def _require_root(self, conn: asyncssh.SSHClientConnection) -> None:
        output = await self._run(conn, "id -u")
        if output.strip() != "0":
            raise RuntimeError("ssh user must be root (or use root credentials)")

    async def _install_dagger_binary(self, conn: asyncssh.SSHClientConnection) -> None:
        script = "\n".join(
            [
                "set -euo pipefail",
                f"wget -q -O /tmp/DaggerConnect {shlex.quote(self.settings.dagger_binary_url)}",
                "install -m 0755 /tmp/DaggerConnect /usr/local/bin/DaggerConnect",
                "rm -f /tmp/DaggerConnect",
                "mkdir -p /etc/DaggerConnect",
            ]
        )
        await self._run_script(conn, script)

    async def _write_remote_file(self, conn: asyncssh.SSHClientConnection, path: str, content: str) -> None:
        async with conn.start_sftp_client() as sftp:
            async with sftp.open(path, "w") as remote_file:
                await remote_file.write(content)

    async def _cleanup_failed_client(self, conn: asyncssh.SSHClientConnection) -> None:
        script = "\n".join(
            [
                "set +e",
                "systemctl stop DaggerConnect-client",
                "systemctl disable DaggerConnect-client",
                "rm -f /etc/DaggerConnect/client.yaml",
                "rm -f /etc/systemd/system/DaggerConnect-client.service",
                "systemctl daemon-reload",
            ]
        )
        await self._run_script(conn, script, check=False)

    async def _safe_cleanup_on_error(self, conn: asyncssh.SSHClientConnection) -> None:
        try:
            await self._cleanup_failed_client(conn)
        except Exception:
            pass

    async def _run_script(self, conn: asyncssh.SSHClientConnection, script: str, check: bool = True) -> str:
        command = f"bash -lc {shlex.quote(script)}"
        return await self._run(conn, command, check=check)

    async def _run(self, conn: asyncssh.SSHClientConnection, command: str, check: bool = True) -> str:
        result = await conn.run(command, check=check, timeout=self.settings.ssh_command_timeout)
        return (result.stdout or "").strip()

    def _compact_error(self, exc: Exception) -> str:
        text = str(exc).strip().replace("\n", " | ")
        return text[:280] if text else exc.__class__.__name__


def summarize_results(results: List[ServerTestResult]) -> Dict[str, int]:
    summary = {
        "success": 0,
        "failed_pattern": 0,
        "manual_review": 0,
        "ssh_error": 0,
        "setup_error": 0,
    }
    for item in results:
        summary[item.status.value] += 1
    return summary
