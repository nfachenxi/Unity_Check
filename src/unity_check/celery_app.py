from celery import Celery

from unity_check.config import get_settings

settings = get_settings()

celery_app = Celery(
    "unity_check",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["unity_check.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    timezone="Asia/Shanghai",
    enable_utc=False,
)
