from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram.ext import Application

from ..ssh_runner import DaggerSshTester, summarize_results
from .jobs_handlers import (
    CompactQueueLiveMessage,
    MODE_QUANTUMMUX,
    MODE_TUN_BIP,
    serialize_result,
)

logger = logging.getLogger(__name__)
TEST_TIMEOUT_SECONDS = 3600


async def run_target_parallel(
    app: Application,
    *,
    job_id: str,
    target: str,
    target_index: int,
    mode: str,
    live_msg: CompactQueueLiveMessage,
    stop_event: asyncio.Event,
    settings: Any,
) -> None:
    store = app.bot_data["store"]
    job_store = app.bot_data["job_store"]

    servers = store.list_servers()
    if not servers:
        return

    await live_msg.begin_target(target_index, target)

    tester = DaggerSshTester(settings)
    semaphore = asyncio.Semaphore(max(1, settings.max_parallel_servers))

    async def run_single_server(server, server_index: int) -> None:
        async with semaphore:
            if stop_event.is_set():
                return

            await live_msg.begin_server(server_index, server)

            if mode == MODE_TUN_BIP:
                result = await tester.apply_tun_bip_config(
                    server,
                    target_addr=target,
                    psk=settings.default_psk,
                    on_log_line=live_msg.on_log_line,
                    stop_event=stop_event,
                )
            else:
                result = await tester.test_server(
                    server,
                    target_addr=target,
                    psk=settings.default_psk,
                    on_log_line=live_msg.on_log_line,
                    stop_event=stop_event,
                )

            job_store.save_server_result(job_id, target, serialize_result(result))
            await live_msg.finish_server(result)

    tasks = [asyncio.create_task(run_single_server(server, i)) for i, server in enumerate(servers, start=1)]
    if tasks:
        done = await asyncio.gather(*tasks, return_exceptions=True)
        for item in done:
            if isinstance(item, Exception) and not isinstance(item, asyncio.CancelledError):
                logger.exception("Server task failed in job %s target %s: %s", job_id, target, item)

    await live_msg.finish_target(target)


async def run_job_queue(app: Application, chat_id: int, job_id: str) -> None:
    job_store = app.bot_data["job_store"]
    active_jobs = app.bot_data["active_jobs"]
    settings = app.bot_data["settings"]

    if chat_id not in active_jobs:
        return

    runtime = active_jobs[chat_id]
    job = job_store.get_job(job_id)
    if job is None:
        active_jobs.pop(chat_id, None)
        return

    server_total = len(app.bot_data["store"].list_servers())
    job_store.set_running(job_id)

    live_msg = CompactQueueLiveMessage(
        app=app,
        chat_id=chat_id,
        job_id=job_id,
        target_total=len(job.targets),
        server_total=server_total,
        mode_label=MODE_QUANTUMMUX if job.mode == MODE_QUANTUMMUX else MODE_TUN_BIP,
        show_counters=(job.mode == MODE_QUANTUMMUX),
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
            final_status = "failed"
        elif stopped:
            final_status = "stopped"
        else:
            final_status = "completed"

        job_store.update_job_status(job_id, final_status)
        await live_msg.finish_queue(stopped=stopped)

        final_job = job_store.get_job(job_id)
        if final_job is not None:
            all_results: dict[str, dict] = {}
            for batch in final_job.completed_batches:
                for res in batch.results:
                    key = f"{batch.target}_{res.get('server_id', 'unknown')}"
                    all_results[key] = res

            summary = summarize_results(all_results, final_job.mode)
            if len(summary) <= 3500:
                await app.bot.send_message(chat_id=chat_id, text=summary)
            else:
                for i in range(0, len(summary), 3500):
                    await app.bot.send_message(chat_id=chat_id, text=summary[i : i + 3500])
