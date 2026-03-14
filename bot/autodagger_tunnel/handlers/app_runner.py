from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from telegram.ext import Application

from ..runtime import get_active_jobs, get_job_store, get_settings, get_store
from ..settings import Settings
from ..ssh_runner import DaggerSshTester, ServerTestResult, summarize_results
from ..models import ServerRecord
from ..utils.ui import MENU, transport_label
from .jobs_handlers import CompactQueueLiveMessage, MODE_GHOSTMUX, MODE_QUANTUMMUX, MODE_TUN_BIP, serialize_result

logger = logging.getLogger(__name__)
TEST_TIMEOUT_SECONDS = 3600

LiveBeginServer = Callable[[int, ServerRecord], Awaitable[None]]
LiveLogLine = Callable[[str], Awaitable[None]]
LiveFinishServer = Callable[[ServerTestResult], Awaitable[None]]


async def run_target_parallel(
    app: Application,
    *,
    job_id: str,
    target: str,
    target_index: int,
    mode: str,
    live_msg: CompactQueueLiveMessage,
    stop_event: asyncio.Event,
    settings: Settings,
) -> None:
    store = get_store(app)
    job_store = get_job_store(app)

    base_mode = mode.split(":")[0] if ":" in mode else mode
    server_id_str = mode.split(":")[1] if ":" in mode else "all"

    all_servers = store.list_servers()
    if server_id_str != "all":
        try:
            target_id = int(server_id_str)
        except ValueError:
            return
        servers = [s for s in all_servers if s.id == target_id]
    else:
        servers = all_servers

    if not servers:
        return

    await live_msg.begin_target(target_index, target)

    tester = DaggerSshTester(settings)
    semaphore = asyncio.Semaphore(max(1, settings.max_parallel_servers))
    live_lock = asyncio.Lock()

    async def begin_server(index: int, server: ServerRecord) -> None:
        async with live_lock:
            await live_msg.begin_server(index, server)

    async def on_log_line(line: str) -> None:
        async with live_lock:
            await live_msg.on_log_line(line)

    async def finish_server(result: ServerTestResult) -> None:
        async with live_lock:
            await live_msg.finish_server(result)

    async def run_single_server(server, server_index: int) -> None:
        async with semaphore:
            if stop_event.is_set():
                return

            await begin_server(server_index, server)

            if base_mode == MODE_TUN_BIP:
                result = await tester.apply_tun_bip_config(
                    server,
                    target_addr=target,
                    psk=settings.default_psk,
                    on_log_line=on_log_line,
                    stop_event=stop_event,
                )
            else:
                result = await tester.test_server(
                    server,
                    target_addr=target,
                    psk=settings.default_psk,
                    transport=base_mode,
                    on_log_line=on_log_line,
                    stop_event=stop_event,
                )

            job_store.save_server_result(job_id, target, serialize_result(result))
            await finish_server(result)

    tasks = [asyncio.create_task(run_single_server(server, i)) for i, server in enumerate(servers, start=1)]
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for item in results:
            if isinstance(item, Exception) and not isinstance(item, asyncio.CancelledError):
                logger.exception("Server task failed in job %s target %s: %s", job_id, target, item)

    await live_msg.finish_target(target)


async def run_job_queue(app: Application, chat_id: int, job_id: str) -> None:
    job_store = get_job_store(app)
    active_jobs = get_active_jobs(app)
    settings = get_settings(app)

    runtime = active_jobs.get(chat_id)
    if runtime is None:
        return

    job = job_store.get_job(job_id)
    if job is None:
        active_jobs.pop(chat_id, None)
        return

    base_mode = job.mode.split(":")[0] if ":" in job.mode else job.mode
    server_id_str = job.mode.split(":")[1] if ":" in job.mode else "all"

    all_servers = get_store(app).list_servers()
    if server_id_str != "all":
        try:
            target_id = int(server_id_str)
            server_total = len([s for s in all_servers if s.id == target_id])
        except ValueError:
            server_total = 0
    else:
        server_total = len(all_servers)

    job_store.set_running(job_id)

    live_msg = CompactQueueLiveMessage(
        app=app,
        chat_id=chat_id,
        job_id=job_id,
        target_total=len(job.targets),
        server_total=server_total,
        mode_label=transport_label(job.mode),
        show_counters=(base_mode in {MODE_QUANTUMMUX, MODE_GHOSTMUX}),
    )
    await live_msg.start()

    stopped = False
    failed = False
    pending_targets = list(job.pending_targets)

    try:
        async with asyncio.timeout(TEST_TIMEOUT_SECONDS):
            for index, target in enumerate(pending_targets, start=1):
                if runtime.stop_event.is_set():
                    stopped = True
                    break

                await run_target_parallel(
                    app,
                    job_id=job_id,
                    target=target,
                    target_index=index,
                    mode=job.mode,
                    live_msg=live_msg,
                    stop_event=runtime.stop_event,
                    settings=settings,
                )

                if runtime.stop_event.is_set():
                    stopped = True
                    break

                job_store.mark_target_done(job_id, target)

    except asyncio.TimeoutError:
        stopped = True
        logger.warning("Queue timed out for job %s", job_id)
    except Exception as exc:  # noqa: BLE001
        failed = True
        logger.exception("Queue failed for job %s: %s", job_id, exc)
    finally:
        active_jobs.pop(chat_id, None)

        if failed:
            final_status = job_store.STATUS_FAILED
        elif stopped:
            final_status = job_store.STATUS_STOPPED
        else:
            final_status = job_store.STATUS_COMPLETED

        job_store.update_job_status(job_id, final_status)
        await live_msg.finish_queue(stopped=stopped)

        final_job = job_store.get_job(job_id)
        if final_job is not None:
            all_results: dict[str, dict] = {}
            for batch in final_job.completed_batches:
                for result in batch.results:
                    key = f"{batch.target}_{result.get('server_id', 'unknown')}"
                    all_results[key] = result

            summary = summarize_results(all_results, base_mode)
            if len(summary) <= 3500:
                await app.bot.send_message(chat_id=chat_id, text=summary, reply_markup=MENU)
            else:
                for index in range(0, len(summary), 3500):
                    chunk = summary[index : index + 3500]
                    include_menu = (index + 3500) >= len(summary)
                    await app.bot.send_message(chat_id=chat_id, text=chunk, reply_markup=MENU if include_menu else None)
