from __future__ import annotations

import asyncio
import re
import shlex
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Dict, List, Optional

import asyncssh

from .log_analyzer import AnalyzerSnapshot, DaggerLogAnalyzer
from .models import ServerRecord
from .settings import Settings
from .templates import render_client_yaml, render_client_yaml_tun_bip, render_service_unit

OnLogLine = Callable[[str], Awaitable[None]]
MAC_RE = re.compile(r"^(?i:[0-9a-f]{2}(?::[0-9a-f]{2}){5})$")


class TestStatus(str, Enum):
    CONFIGURED = "configured"
    SUCCESS = "success"
    FAILED_PATTERN = "failed_pattern"
    MANUAL_REVIEW = "manual_review"
    SSH_ERROR = "ssh_error"
    SETUP_ERROR = "setup_error"
    CANCELLED = "cancelled"


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
        stop_event: Optional[asyncio.Event] = None,
    ) -> ServerTestResult:
        attempts = max(1, self.settings.ssh_max_retries)
        last_result: Optional[ServerTestResult] = None

        for attempt in range(1, attempts + 1):
            if self._is_stopped(stop_event):
                return self._cancelled_result(server, target_addr)

            result = await self._test_server_once(
                server,
                target_addr,
                psk,
                on_log_line=on_log_line,
                stop_event=stop_event,
            )
            last_result = result

            if not self._should_retry_result(result, attempt, attempts):
                return result

            backoff = self.settings.ssh_retry_backoff_seconds * (2 ** (attempt - 1))
            if on_log_line is not None:
                await on_log_line(
                    f"[retry] transient_error detected. attempt {attempt}/{attempts} "
                    f"-> next retry in {backoff:.1f}s"
                )
            await asyncio.sleep(backoff)

        return last_result or self._setup_error_result(server, target_addr, "unknown_retry_failure")

    async def _test_server_once(
        self,
        server: ServerRecord,
        target_addr: str,
        psk: str,
        *,
        on_log_line: Optional[OnLogLine],
        stop_event: Optional[asyncio.Event],
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
            return self._ssh_error_result(server, target_addr, f"ssh_connect_failed: {self._compact_error(exc)}")

        try:
            if self._is_stopped(stop_event):
                return self._cancelled_result(server, target_addr)

            await self._run_preflight(conn, mode="quantummux")
            await self._install_dagger_binary(conn)
            qm_hints = await self._detect_quantummux_hints(conn)

            if self._is_stopped(stop_event):
                return self._cancelled_result(server, target_addr)

            await self._write_remote_file(
                conn,
                "/etc/DaggerConnect/client.yaml",
                render_client_yaml(
                    target_addr,
                    psk,
                    interface=qm_hints["interface"],
                    local_ip=qm_hints["local_ip"],
                    router_mac=qm_hints["router_mac"],
                ),
            )
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
                stop_event=stop_event,
            )

            if self._is_stopped(stop_event):
                await self._cleanup_failed_client(conn)
                return self._cancelled_result(
                    server,
                    target_addr,
                    reason="cancelled_by_user_after_partial_log_capture",
                    analyzer=analyzer.snapshot(),
                    log_tail=log_tail,
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

            return self._setup_error_result(
                server,
                target_addr,
                f"setup_or_runtime_error: {self._compact_error(exc)}",
            )
        finally:
            if conn is not None:
                conn.close()
                try:
                    await conn.wait_closed()
                except Exception:
                    pass

    async def apply_tun_bip_config(
        self,
        server: ServerRecord,
        target_addr: str,
        psk: str,
        *,
        on_log_line: Optional[OnLogLine] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> ServerTestResult:
        attempts = max(1, self.settings.ssh_max_retries)
        last_result: Optional[ServerTestResult] = None

        for attempt in range(1, attempts + 1):
            if self._is_stopped(stop_event):
                return self._cancelled_result(server, target_addr)

            result = await self._apply_tun_bip_once(
                server,
                target_addr,
                psk,
                stop_event=stop_event,
            )
            last_result = result

            if not self._should_retry_result(result, attempt, attempts):
                return result

            backoff = self.settings.ssh_retry_backoff_seconds * (2 ** (attempt - 1))
            if on_log_line is not None:
                await on_log_line(
                    f"[retry] transient_error detected. attempt {attempt}/{attempts} "
                    f"-> next retry in {backoff:.1f}s"
                )
            await asyncio.sleep(backoff)

        return last_result or self._setup_error_result(server, target_addr, "unknown_retry_failure")

    async def _apply_tun_bip_once(
        self,
        server: ServerRecord,
        target_addr: str,
        psk: str,
        *,
        stop_event: Optional[asyncio.Event],
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
            return self._ssh_error_result(server, target_addr, f"ssh_connect_failed: {self._compact_error(exc)}")

        try:
            if self._is_stopped(stop_event):
                return self._cancelled_result(server, target_addr)

            await self._run_preflight(conn, mode="tun_bip")
            await self._install_dagger_binary(conn)

            dest_ip, health_port = self._split_target_addr(target_addr)

            if self._is_stopped(stop_event):
                return self._cancelled_result(server, target_addr)

            await self._write_remote_file(
                conn,
                "/etc/DaggerConnect/client.yaml",
                render_client_yaml_tun_bip(
                    target_addr,
                    psk,
                    dest_ip=dest_ip,
                    health_port=health_port,
                ),
            )
            await self._write_remote_file(
                conn,
                "/etc/systemd/system/DaggerConnect-client.service",
                render_service_unit(),
            )
            await self._run(conn, "systemctl daemon-reload")
            await self._run(conn, "systemctl restart DaggerConnect-client")
            await self._run(conn, "systemctl is-active DaggerConnect-client")

            return ServerTestResult(
                server_id=server.id,
                server_name=server.name,
                host=server.host,
                port=server.port,
                target_addr=target_addr,
                status=TestStatus.CONFIGURED,
                reason="tun_bip_client_config_applied",
            )

        except Exception as exc:  # noqa: BLE001
            if conn is not None:
                await self._safe_cleanup_on_error(conn)

            return self._setup_error_result(
                server,
                target_addr,
                f"setup_or_runtime_error: {self._compact_error(exc)}",
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
        stop_event: Optional[asyncio.Event],
    ) -> tuple[DaggerLogAnalyzer, List[str]]:
        analyzer = DaggerLogAnalyzer()
        log_tail: deque[str] = deque(maxlen=40)
        proc = await conn.create_process("journalctl -u DaggerConnect-client -n 0 -f --no-pager -o cat")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds

        while loop.time() < deadline:
            if self._is_stopped(stop_event):
                break

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

    async def _run_preflight(self, conn: asyncssh.SSHClientConnection, *, mode: str) -> None:
        await self._require_root(conn)

        required_commands = {"bash", "install", "mkdir", "systemctl", "wget", "df"}
        if mode == "quantummux":
            required_commands.update({"ip", "journalctl"})
        elif mode == "tun_bip":
            required_commands.update({"iptables"})

        missing = await self._find_missing_commands(conn, sorted(required_commands))
        if missing:
            raise RuntimeError(f"preflight_missing_commands: {','.join(missing)}")

        preflight_script = "\n".join(
            [
                "set -euo pipefail",
                "# Check for minimal 50MB free disk space on root filesystem",
                "ROOT_FREE=$(df -m / | awk 'NR==2 {print $4}')",
                "if [[ -n \"$ROOT_FREE\" ]] && [[ \"$ROOT_FREE\" -lt 50 ]]; then",
                "  echo 'insufficient_disk_space: less than 50MB free on /' >&2",
                "  exit 1",
                "fi",
                "mkdir -p /etc/DaggerConnect",
                "touch /etc/DaggerConnect/.autodagger_preflight",
                "rm -f /etc/DaggerConnect/.autodagger_preflight",
                "test -w /etc",
                "test -w /usr/local/bin",
            ]
        )
        await self._run_script(conn, preflight_script)

    async def _find_missing_commands(
        self,
        conn: asyncssh.SSHClientConnection,
        commands: list[str],
    ) -> list[str]:
        missing: list[str] = []
        for cmd in commands:
            result = await self._run_result(conn, f"command -v {shlex.quote(cmd)} >/dev/null 2>&1", check=False)
            if result.exit_status != 0:
                missing.append(cmd)
        return missing

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

    async def _detect_quantummux_hints(self, conn: asyncssh.SSHClientConnection) -> Dict[str, str]:
        script = "\n".join(
            [
                "set +e",
                "iface=$(ip route show default 0.0.0.0/0 2>/dev/null | awk 'NR==1 {print $5}')",
                "if [[ -z \"$iface\" ]]; then iface=$(ip -6 route show default 2>/dev/null | awk 'NR==1 {print $5}'); fi",
                "local_ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i==\"src\"){print $(i+1); exit}}')",
                "if [[ -z \"$local_ip\" && -n \"$iface\" ]]; then local_ip=$(ip -4 addr show dev \"$iface\" 2>/dev/null | awk '/inet /{print $2}' | head -n1 | cut -d/ -f1); fi",
                "gateway_ip=$(ip route show default 0.0.0.0/0 2>/dev/null | awk 'NR==1 {print $3}')",
                "router_mac=''",
                "if [[ -n \"$gateway_ip\" && -n \"$iface\" ]]; then",
                "  router_mac=$(ip neigh show \"$gateway_ip\" dev \"$iface\" 2>/dev/null | awk 'NR==1 {print $5}')",
                "  if [[ -z \"$router_mac\" || \"$router_mac\" == \"FAILED\" || \"$router_mac\" == \"INCOMPLETE\" ]]; then",
                "    ping -c1 -W1 \"$gateway_ip\" >/dev/null 2>&1 || true",
                "    router_mac=$(ip neigh show \"$gateway_ip\" dev \"$iface\" 2>/dev/null | awk 'NR==1 {print $5}')",
                "  fi",
                "fi",
                "printf 'interface=%s\\nlocal_ip=%s\\nrouter_mac=%s\\n' \"$iface\" \"$local_ip\" \"$router_mac\"",
            ]
        )
        output = await self._run_script(conn, script, check=False)

        hints: Dict[str, str] = {"interface": "", "local_ip": "", "router_mac": ""}
        for raw_line in output.splitlines():
            if "=" not in raw_line:
                continue
            key, value = raw_line.split("=", 1)
            key = key.strip()
            if key not in hints:
                continue
            hints[key] = value.strip()

        mac = hints["router_mac"]
        if not MAC_RE.match(mac):
            hints["router_mac"] = ""

        return hints

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
        result = await self._run_result(conn, command, check=check)
        return (result.stdout or "").strip()

    async def _run_result(
        self,
        conn: asyncssh.SSHClientConnection,
        command: str,
        check: bool = True,
    ) -> asyncssh.SSHCompletedProcess:
        return await conn.run(command, check=check, timeout=self.settings.ssh_command_timeout)

    def _split_target_addr(self, target_addr: str) -> tuple[str, int]:
        host, port_s = target_addr.rsplit(":", 1)
        port = int(port_s)
        return host.strip(), port

    def _is_stopped(self, stop_event: Optional[asyncio.Event]) -> bool:
        return bool(stop_event is not None and stop_event.is_set())

    def _cancelled_result(
        self,
        server: ServerRecord,
        target_addr: str,
        *,
        reason: str = "cancelled_by_user",
        analyzer: Optional[AnalyzerSnapshot] = None,
        log_tail: Optional[List[str]] = None,
    ) -> ServerTestResult:
        return ServerTestResult(
            server_id=server.id,
            server_name=server.name,
            host=server.host,
            port=server.port,
            target_addr=target_addr,
            status=TestStatus.CANCELLED,
            reason=reason,
            analyzer=analyzer or AnalyzerSnapshot(0, 0, 0, 0, ""),
            log_tail=log_tail or [],
        )

    def _ssh_error_result(self, server: ServerRecord, target_addr: str, reason: str) -> ServerTestResult:
        return ServerTestResult(
            server_id=server.id,
            server_name=server.name,
            host=server.host,
            port=server.port,
            target_addr=target_addr,
            status=TestStatus.SSH_ERROR,
            reason=reason,
        )

    def _setup_error_result(self, server: ServerRecord, target_addr: str, reason: str) -> ServerTestResult:
        return ServerTestResult(
            server_id=server.id,
            server_name=server.name,
            host=server.host,
            port=server.port,
            target_addr=target_addr,
            status=TestStatus.SETUP_ERROR,
            reason=reason,
        )

    def _should_retry_result(self, result: ServerTestResult, attempt: int, attempts: int) -> bool:
        if attempt >= attempts:
            return False
        if result.status not in {TestStatus.SSH_ERROR, TestStatus.SETUP_ERROR}:
            return False
        return self._is_transient_reason(result.reason)

    def _is_transient_reason(self, reason: str) -> bool:
        lower = reason.lower()
        hard_fail_markers = (
            "permission denied",
            "authentication failed",
            "preflight_missing_commands",
            "must be root",
            "no route to host",
            "name or service not known",
            "not found",
        )
        if any(marker in lower for marker in hard_fail_markers):
            return False

        transient_markers = (
            "timeout",
            "timed out",
            "connection refused",
            "connection reset",
            "connection lost",
            "connection aborted",
            "temporarily unavailable",
            "broken pipe",
            "failed to connect",
            "network is unreachable",
            "channel open failed",
        )
        return any(marker in lower for marker in transient_markers)

    def _compact_error(self, exc: Exception) -> str:
        text = str(exc).strip().replace("\n", " | ")
        return text[:280] if text else exc.__class__.__name__


def summarize_results(results: Dict[str, dict], mode: str = "quantummux") -> str:
    """Creates a markdown formatted summary of test results."""
    from collections import Counter
    counts = Counter(r.get("status") for r in results.values())
    
    total = len(results)
    if total == 0:
        return "No results."
        
    lines = [
        f"Tunnel Test Summary ({mode})",
        f"Total Servers: {total}",
        f"Success: {counts.get(TestStatus.SUCCESS.value, 0)}",
        f"Configured: {counts.get(TestStatus.CONFIGURED.value, 0)}",
        f"Failed Pattern: {counts.get(TestStatus.FAILED_PATTERN.value, 0)}",
        f"Manual Review: {counts.get(TestStatus.MANUAL_REVIEW.value, 0)}",
        f"SSH Err: {counts.get(TestStatus.SSH_ERROR.value, 0)}",
        f"Setup Err: {counts.get(TestStatus.SETUP_ERROR.value, 0)}",
        f"Cancelled: {counts.get(TestStatus.CANCELLED.value, 0)}",
    ]
    return "\n".join(lines)


async def run_ssh_connectivity_check(
    host: str,
    port: int,
    username: str,
    password: str,
    connect_timeout: int,
) -> tuple[bool, str]:
    """Tests SSH connectivity securely with timeout bounds and cleans up the connection immediately."""
    conn: Optional[asyncssh.SSHClientConnection] = None
    try:
        conn = await asyncssh.connect(
            host,
            port=port,
            username=username,
            password=password,
            known_hosts=None,
            connect_timeout=connect_timeout,
        )
        await conn.run("true", check=True, timeout=max(3, connect_timeout))
        return True, "ssh_connection_successful"
    except Exception as exc:  # noqa: BLE001
        text = str(exc).strip().replace("\n", " | ")
        compact_error_str = text[:280] if text else exc.__class__.__name__
        return False, compact_error_str
    finally:
        if conn is not None:
            conn.close()
            try:
                await conn.wait_closed()
            except Exception:
                pass

