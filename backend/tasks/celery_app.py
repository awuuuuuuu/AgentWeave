from __future__ import annotations

import os
import sys

_backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from celery import Celery

from config import settings

_BROKER_URL = settings.celery_broker_url
_BACKEND_URL = settings.celery_backend_url

celery_app = Celery(
    "ragent",
    broker=_BROKER_URL,
    backend=_BACKEND_URL,
    include=["tasks.ingest", "tasks.cleanup"]
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    worker_max_tasks_per_child=50,  # 每个 worker 进程处理 50 个任务后重启，防止内存泄漏
)