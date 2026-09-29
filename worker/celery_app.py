"""Celery configuration: Redis transport, PostgreSQL result receipts."""

from __future__ import annotations

from celery import Celery

from api.settings import get_settings

settings = get_settings()
celery_app = Celery(
    "mindbridge",
    broker=str(settings.redis_url),
    include=["worker.tasks"],
)
celery_app.conf.update(
    result_backend=None,
    task_ignore_result=True,
    task_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_acks_on_failure_or_timeout=False,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=False,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 3600},
    task_soft_time_limit=540,
    task_time_limit=600,
)
