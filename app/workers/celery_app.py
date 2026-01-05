"""
Celery application configuration.
Configures Celery with Redis backend and task autodiscovery.
"""
from celery import Celery
from celery.signals import task_prerun, task_postrun, task_failure
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Create Celery app
celery_app = Celery(
    "ai_platform_workers",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

# Configure Celery
celery_app.conf.update(
    task_serializer=settings.CELERY_TASK_SERIALIZER,
    result_serializer=settings.CELERY_RESULT_SERIALIZER,
    accept_content=settings.CELERY_ACCEPT_CONTENT,
    timezone=settings.CELERY_TIMEZONE,
    enable_utc=settings.CELERY_ENABLE_UTC,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max
    task_soft_time_limit=3300,  # 55 minutes soft limit
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=100,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

# Auto-discover tasks
# Import tasks explicitly to ensure they are registered
import app.workers.document_pipeline

celery_app.autodiscover_tasks(['app.workers'])


@task_prerun.connect
def task_prerun_handler(sender=None, task_id=None, task=None, args=None, kwargs=None, **extra):
    """Log task start."""
    logger.info(
        "Task started",
        task_id=task_id,
        task_name=task.name,
        environment=settings.APP_ENV,
        event="task_start"
    )


@task_postrun.connect
def task_postrun_handler(sender=None, task_id=None, task=None, args=None, kwargs=None, retval=None, **extra):
    """Log task completion."""
    logger.info(
        "Task completed",
        task_id=task_id,
        task_name=task.name,
        event="task_complete"
    )


@task_failure.connect
def task_failure_handler(sender=None, task_id=None, exception=None, args=None, kwargs=None, traceback=None, einfo=None, **extra):
    """Log task failure."""
    logger.error(
        "Task failed",
        task_id=task_id,
        task_name=sender.name,
        error=str(exception),
        error_type=type(exception).__name__,
        event="task_error",
        exc_info=True
    )
