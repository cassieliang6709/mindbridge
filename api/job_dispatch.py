"""Small producer boundary used by HTTP routes and reconciliation."""

from __future__ import annotations

from .background_jobs import BackgroundJob, BackgroundJobStore


def send_background_job(job_id: str) -> str:
    from worker.celery_app import celery_app

    result = celery_app.send_task("mindbridge.run_background_job", args=[job_id])
    return str(result.id)


async def dispatch_jobs(
    store: BackgroundJobStore, jobs: list[BackgroundJob]
) -> tuple[list[BackgroundJob], list[dict[str, str]]]:
    sent: list[BackgroundJob] = []
    errors: list[dict[str, str]] = []
    for job in jobs:
        try:
            task_id = send_background_job(job.job_id)
            await store.attach_delivery(job.job_id, task_id)
            sent.append((await store.get(job.job_id)) or job)
        except Exception as error:
            # The durable row intentionally stays queued. A later reconciliation
            # can send it once Redis is reachable.
            errors.append({"job_id": job.job_id, "error": str(error)})
    return sent, errors
