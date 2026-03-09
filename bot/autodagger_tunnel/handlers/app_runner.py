from __future__ import annotations

import asyncio
from telegram.ext import ContextTypes

from ..db import JobStore
from ..ssh_runner import DaggerSshTester, summarize_results
from .jobs_handlers import (
    CompactQueueLiveMessage, 
    get_active_jobs, 
    get_job_store, 
    serialize_result,
    MODE_QUANTUMMUX,
    MODE_TUN_BIP
)
from .servers_handlers import get_store, get_settings

TEST_TIMEOUT_SECONDS = 3600

async def run_target_parallel(
    app,
    job_id: str,
    target: str,
    target_index: int,
    live_msg: CompactQueueLiveMessage,
    stop_event: asyncio.Event,
    settings,
):
    """
    Executes tests against all servers for a single target concurrently up to MAX_PARALLEL_SERVERS.
    """
    store = app.bot_data["store"]
    servers = store.list_servers()
    if not servers:
        return

    await live_msg.begin_target(target_index, target)
    
    # We constrain the number of parallel SSH tests to prevent connection bursts and overload. 
    # The user request asks for Concurrency explicitly (e.g., 3 max).
    max_concurrency = 3
    semaphore = asyncio.Semaphore(max_concurrency)
    
    job_store = app.bot_data["job_store"]
    test_mode = live_msg.mode_label

    async def run_single_server(server, server_index: int):
        async with semaphore:
            if stop_event.is_set():
                return
            await live_msg.begin_server(server_index, server)

            tester = DaggerSshTester(
                server=server,
                target_addr=target,
                transport=test_mode,
                settings=settings,
            )

            result = await tester.test_server(
                stop_event=stop_event,
                on_log_line=live_msg.on_log_line,
            )
            
            job_store.save_server_result(job_id, target, serialize_result(result))
            await live_msg.finish_server(result)

    tasks = []
    for i, srv in enumerate(servers, start=1):
        tasks.append(run_single_server(srv, i))
    
    if tasks:
        await asyncio.gather(*tasks)

    await live_msg.finish_target(target)

async def run_job_queue(app: ContextTypes.DEFAULT_TYPE.application, chat_id: int, job_id: str) -> None:
    job_store = app.bot_data["job_store"]
    active_jobs = app.bot_data["active_jobs"]
    settings = app.bot_data["settings"]

    server_store = app.bot_data["store"]
    server_total = len(server_store.list_servers())

    if chat_id not in active_jobs:
        return

    runtime = active_jobs[chat_id]
    job = job_store.get_job(job_id)

    if job is None:
        active_jobs.pop(chat_id, None)
        return

    job_store.update_job_status(job_id, "running")

    mode_label = MODE_QUANTUMMUX if job.mode == MODE_QUANTUMMUX else MODE_TUN_BIP
    live_msg = CompactQueueLiveMessage(
        app=app,
        chat_id=chat_id,
        job_id=job_id,
        target_total=len(job.pending_targets),
        server_total=server_total,
        mode_label=mode_label,
        show_counters=(job.mode == MODE_QUANTUMMUX),
    )

    await live_msg.start()

    stopped = False
    target_count = len(job.pending_targets)

    try:
        async with asyncio.timeout(TEST_TIMEOUT_SECONDS):
            for i, target in enumerate(job.pending_targets, start=1):
                if runtime.stop_event.is_set():
                    stopped = True
                    break

                # Ensure Job Status transitions from 'running' 
                await run_target_parallel(
                    app=app,
                    job_id=job_id,
                    target=target,
                    target_index=i,
                    live_msg=live_msg,
                    stop_event=runtime.stop_event,
                    settings=settings
                )

                if runtime.stop_event.is_set():
                    stopped = True
                    break

                job_store.mark_target_done(job_id, target)
    except asyncio.TimeoutError:
        stopped = True
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception("Queue failed %s", exc)
        stopped = True
    finally:
        active_jobs.pop(chat_id, None)
        final_status = "stopped" if stopped else "completed"
        job_store.update_job_status(job_id, final_status)
        await live_msg.finish_queue(stopped=stopped)

        final_job = job_store.get_job(job_id)
        if final_job is not None:
            all_results = {}
            for batch in final_job.completed_batches:
                for res in batch.results:
                    all_results[f"{batch.target}_{res.get('server_id')}"] = res

            summary = summarize_results(all_results, mode_label)
            try:
                if len(summary) > 3500:
                    chunks = [summary[i : i + 3500] for i in range(0, len(summary), 3500)]
                    for chunk in chunks:
                        await app.bot.send_message(
                            chat_id=chat_id,
                            text=chunk,
                            parse_mode="MarkdownV2" if "bold" in chunk else None,
                        )
                else:
                    await app.bot.send_message(
                        chat_id=chat_id, text=summary, parse_mode="MarkdownV2"
                    )
            except Exception as sm_exc:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=f"Failed to send formatted summary. Raw:\n{summary[-3000:]}"
                )
