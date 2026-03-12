from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler

from ..db import JobStore
from ..models import JobRecord, ServerRecord
from ..runtime import ActiveJobContext, get_active_jobs, get_job_store
from ..ssh_runner import ServerTestResult, TestStatus
from ..utils.ui import (
    BTN_STOP,
    ICON_CHART,
    ICON_ID,
    ICON_INFO,
    ICON_LIST,
    ICON_NOTE,
    ICON_PC,
    ICON_PLAY,
    ICON_RADAR,
    ICON_ROCKET,
    ICON_STOP,
    ICON_TARGET,
    ICON_WAIT,
    ICON_WARN,
    MENU,
    transport_label,
)
from ..utils.validators import parse_targets_input, parse_transport_choice
from .servers_handlers import check_access, get_store

TEST_TRANSPORT, TEST_TARGET = range(10, 12)
MODE_QUANTUMMUX = "quantummux"
MODE_TUN_BIP = "tun_bip"
ATTEMPT_RE = re.compile(r"attempt #(\d+)", re.IGNORECASE)


def serialize_result(result: ServerTestResult) -> dict:
    return {
        "server_id": result.server_id,
        "server_name": result.server_name,
        "host": result.host,
        "port": result.port,
        "target_addr": result.target_addr,
        "status": result.status.value,
        "reason": result.reason,
        "analyzer": {
            "connected_count": result.analyzer.connected_count,
            "disconnected_count": result.analyzer.disconnected_count,
            "reconnect_count": result.analyzer.reconnect_count,
            "streams_zero_count": result.analyzer.streams_zero_count,
            "failure_reason": result.analyzer.failure_reason,
        },
        "log_tail": list(result.log_tail),
    }


def generate_progress_bar(current: int, total: int, length: int = 10) -> str:
    if total == 0:
        return f"[{'-' * length}] 0%"
    filled = int(round(length * current / float(total)))
    bar = "#" * filled + "-" * (length - filled)
    percent = int(round(100.0 * current / float(total)))
    return f"[{bar}] {percent}%"


class CompactQueueLiveMessage:
    def __init__(
        self,
        app,
        chat_id: int,
        job_id: str,
        target_total: int,
        server_total: int,
        *,
        mode_label: str,
        show_counters: bool,
    ) -> None:
        self.app = app
        self.chat_id = chat_id
        self.job_id = job_id
        self.target_total = target_total
        self.server_total = server_total
        self.mode_label = mode_label
        self.show_counters = show_counters

        self.message_id: Optional[int] = None
        self.started_at = time.monotonic()
        self.last_flush = 0.0

        self.target_index = 0
        self.current_target = "-"
        self.server_index = 0
        self.current_server = "-"
        self.current_state = "idle"
        self.latest_signal = "-"

        self.connected_count = 0
        self.disconnected_count = 0
        self.reconnect_count = 0
        self.streams_zero_count = 0
        self.oom_count = 0

        self.target_done = 0
        self.target_success = 0
        self.target_failed = 0
        self.target_review = 0
        self.target_ssh = 0
        self.target_setup = 0

    async def start(self) -> None:
        msg = await self.app.bot.send_message(chat_id=self.chat_id, text=self._render())
        self.message_id = msg.message_id

    async def begin_target(self, target_index: int, target: str) -> None:
        self.target_index = target_index
        self.current_target = target
        self.server_index = 0
        self.current_server = "-"
        self.current_state = "target_started"
        self.latest_signal = "target queued"

        self.target_done = 0
        self.target_success = 0
        self.target_failed = 0
        self.target_review = 0
        self.target_ssh = 0
        self.target_setup = 0
        await self.flush(force=True)

    async def begin_server(self, server_index: int, server: ServerRecord) -> None:
        self.server_index = server_index
        self.current_server = f"{server.name} ({server.host}:{server.port})"
        self.current_state = "running"
        self.latest_signal = "waiting for diagnostics"

        self.connected_count = 0
        self.disconnected_count = 0
        self.reconnect_count = 0
        self.streams_zero_count = 0
        self.oom_count = 0
        await self.flush(force=True)

    async def on_log_line(self, line: str) -> None:
        event = self._extract_event(line)
        if event is None:
            return

        event_type, event_text = event
        if event_type == "connected":
            self.connected_count += 1
        elif event_type == "disconnected":
            self.disconnected_count += 1
        elif event_type == "reconnect":
            self.reconnect_count += 1
        elif event_type == "streams_zero":
            self.streams_zero_count += 1
        elif event_type == "oom":
            self.oom_count += 1

        self.latest_signal = event_text
        await self.flush()

    async def finish_server(self, result: ServerTestResult) -> None:
        self.target_done += 1
        if result.status in {TestStatus.SUCCESS, TestStatus.CONFIGURED}:
            self.target_success += 1
            self.current_state = "server_done_success"
            self.latest_signal = "CONFIG_APPLIED" if result.status == TestStatus.CONFIGURED else "SUCCESS detected"
        elif result.status == TestStatus.FAILED_PATTERN:
            self.target_failed += 1
            self.current_state = "server_done_failed_pattern"
            self.latest_signal = "FAILED pattern detected"
        elif result.status == TestStatus.MANUAL_REVIEW:
            self.target_review += 1
            self.current_state = "server_done_manual_review"
            self.latest_signal = "manual review needed"
        elif result.status == TestStatus.SSH_ERROR:
            self.target_ssh += 1
            self.current_state = "server_done_ssh_error"
            self.latest_signal = "SSH error"
        else:
            self.target_setup += 1
            self.current_state = "server_done_setup_error"
            self.latest_signal = "setup/runtime error"

        await self.flush(force=True)

    async def finish_target(self, target: str) -> None:
        self.current_target = target
        self.current_state = "target_done"
        if self.target_success > 0:
            self.latest_signal = f"target completed with {self.target_success} successful server(s)"
        else:
            self.latest_signal = "target completed with no successful server"
        await self.flush(force=True)

    async def finish_queue(self, *, stopped: bool = False) -> None:
        self.current_state = "queue_stopped" if stopped else "queue_done"
        self.latest_signal = "queue stopped by user" if stopped else "queue completed"
        await self.flush(force=True)

    async def flush(self, force: bool = False) -> None:
        if self.message_id is None:
            return
        now = time.monotonic()
        if not force and now - self.last_flush < 1.0:
            return

        try:
            await self.app.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.message_id,
                text=self._render(),
            )
            self.last_flush = now
        except BadRequest as exc:
            if "Message is not modified" not in str(exc):
                import logging

                logging.getLogger("autodagger_tunnel.ui").warning("Live log update failed: %s", exc)

    def _render(self) -> str:
        elapsed = int(time.monotonic() - self.started_at)
        progress_bar = generate_progress_bar(self.target_done, self.server_total)

        lines = [
            f"{ICON_RADAR} Tunnel test live status",
            f"{ICON_ID} Job ID: {self.job_id}",
            f"{ICON_NOTE} Mode: {self.mode_label}",
            f"{ICON_TARGET} Target: {self.target_index}/{self.target_total} -> {self.current_target}",
            f"{ICON_PC} Server [{self.server_index}/{self.server_total}]: {self.current_server}",
            f"Progress: {progress_bar}",
            f"{ICON_INFO} State: {self.current_state}",
            f"{ICON_INFO} Signal: {self.latest_signal}",
        ]
        if self.show_counters:
            lines.append(
                f"{ICON_CHART} Counters: c={self.connected_count} d={self.disconnected_count} "
                f"r={self.reconnect_count} s0={self.streams_zero_count} oom={self.oom_count}"
            )
        lines.extend(
            [
                f"{ICON_CHART} Tally: ok={self.target_success} fail={self.target_failed} review={self.target_review} "
                f"ssh={self.target_ssh} setup={self.target_setup}",
                f"{ICON_WAIT} Elapsed: {elapsed}s",
            ]
        )
        return "\n".join(lines)

    def _extract_event(self, line: str) -> tuple[str, str] | None:
        lower = line.lower()
        if "oom-kill" in lower or "failed with result 'oom-kill'" in lower:
            return "oom", "OOM_KILL detected"
        if "] connected " in lower:
            return "connected", "CONNECTED detected"
        if "] disconnected " in lower:
            return "disconnected", "DISCONNECTED detected"
        if "reconnect in" in lower:
            attempt = ATTEMPT_RE.search(line)
            if attempt:
                return "reconnect", f"RECONNECT detected (#{attempt.group(1)})"
            return "reconnect", "RECONNECT detected"
        if "streams=0" in lower:
            return "streams_zero", "STREAMS_ZERO detected"
        return None


async def test_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_access(update, context):
        return ConversationHandler.END

    active_jobs = get_active_jobs(context)
    chat_id = update.effective_chat.id
    if chat_id in active_jobs:
        await update.effective_message.reply_text(
            f"{ICON_WAIT} A test is already running in this chat. Wait until it finishes.",
            reply_markup=MENU,
        )
        return ConversationHandler.END

    if not get_store(context).list_servers():
        await update.effective_message.reply_text(
            f"{ICON_LIST} No servers saved yet. Add server first.",
            reply_markup=MENU,
        )
        return ConversationHandler.END

    await update.effective_message.reply_text(
        f"{ICON_NOTE} Select tunnel mode:\n"
        "1) quantummux (auto log check)\n"
        "2) tun + bip (config only, manual test)"
    )
    return TEST_TRANSPORT


async def test_receive_transport(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    mode = parse_transport_choice(update.effective_message.text)
    if mode is None:
        await update.effective_message.reply_text(
            f"{ICON_WARN} Invalid mode. Send 1 for quantummux or 2 for tun+bip."
        )
        return TEST_TRANSPORT

    context.user_data["test_mode"] = mode
    await update.effective_message.reply_text(
        f"{ICON_TARGET} Send one or multiple target address:port values.\n"
        "Examples:\n"
        "- 203.0.113.10:443\n"
        "- 203.0.113.10:443, 198.51.100.20:8443"
    )
    return TEST_TARGET


async def test_receive_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw_input = update.effective_message.text.strip()
    targets, invalid = parse_targets_input(raw_input)

    if invalid:
        bad = ", ".join(invalid[:6])
        await update.effective_message.reply_text(
            f"{ICON_WARN} Invalid target(s): {bad}\nUse format IP:PORT and try again."
        )
        return TEST_TARGET

    if not targets:
        await update.effective_message.reply_text(f"{ICON_WARN} No valid target found. Try again.")
        return TEST_TARGET

    chat_id = update.effective_chat.id
    mode = context.user_data.get("test_mode", MODE_QUANTUMMUX)
    job_store = get_job_store(context)
    active_jobs = get_active_jobs(context)

    job = job_store.create_job(chat_id=chat_id, mode=mode, targets=targets)
    runtime = ActiveJobContext(
        job_id=job.job_id,
        chat_id=chat_id,
        mode=mode,
        stop_event=asyncio.Event(),
    )

    from .app_runner import run_job_queue

    task = context.application.create_task(run_job_queue(context.application, chat_id, job.job_id))
    runtime.task = task
    active_jobs[chat_id] = runtime

    await update.effective_message.reply_text(
        f"{ICON_ROCKET} Queue started in mode: {transport_label(mode)}\n"
        f"{ICON_ID} Job ID: {job.job_id}\n"
        f"{ICON_PLAY} Use /resume {job.job_id} if bot restarts.\n"
        f"{ICON_STOP} Use '{BTN_STOP}' to stop this job.",
        reply_markup=MENU,
    )

    return ConversationHandler.END


async def stop_current_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return

    chat_id = update.effective_chat.id
    runtime = get_active_jobs(context).get(chat_id)
    if runtime is None:
        await update.effective_message.reply_text(f"{ICON_INFO} No active job in this chat.", reply_markup=MENU)
        return

    if runtime.stop_event.is_set():
        await update.effective_message.reply_text(
            f"{ICON_WAIT} Stop already requested for job {runtime.job_id}.",
            reply_markup=MENU,
        )
        return

    runtime.stop_event.set()
    await update.effective_message.reply_text(
        f"{ICON_STOP} Immediate Stop requested for job {runtime.job_id}. Actively terminating SSH sessions...",
        reply_markup=MENU,
    )


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return

    chat_id = update.effective_chat.id
    active_jobs = get_active_jobs(context)
    if chat_id in active_jobs:
        current = active_jobs[chat_id]
        await update.effective_message.reply_text(
            f"{ICON_WAIT} Job {current.job_id} is already running in this chat.",
            reply_markup=MENU,
        )
        return

    job_store = get_job_store(context)
    requested_id = context.args[0].strip() if context.args else ""
    job: Optional[JobRecord]

    if requested_id:
        job = job_store.get_job(requested_id)
        if job is None or job.chat_id != chat_id:
            await update.effective_message.reply_text(
                f"{ICON_WARN} Job not found for this chat: {requested_id}",
                reply_markup=MENU,
            )
            return
    else:
        job = job_store.get_latest_resumable_job(chat_id)
        if job is None:
            await update.effective_message.reply_text(
                f"{ICON_INFO} No resumable jobs found. Start a new test from menu.",
                reply_markup=MENU,
            )
            return

    if not job.pending_targets:
        await update.effective_message.reply_text(
            f"{ICON_INFO} Job {job.job_id} has no pending targets.",
            reply_markup=MENU,
        )
        return

    if job.status not in JobStore.RESUMABLE_STATUSES:
        await update.effective_message.reply_text(
            f"{ICON_WARN} Job {job.job_id} is not resumable (status={job.status}).",
            reply_markup=MENU,
        )
        return

    runtime = ActiveJobContext(
        job_id=job.job_id,
        chat_id=chat_id,
        mode=job.mode,
        stop_event=asyncio.Event(),
    )

    from .app_runner import run_job_queue

    task = context.application.create_task(run_job_queue(context.application, chat_id, job.job_id))
    runtime.task = task
    active_jobs[chat_id] = runtime

    await update.effective_message.reply_text(
        f"{ICON_PLAY} Resuming job {job.job_id}\n"
        f"Mode: {transport_label(job.mode)}\n"
        f"Pending targets: {len(job.pending_targets)}",
        reply_markup=MENU,
    )
