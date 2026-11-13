"""
Celery Application and Celery Beat Configuration.
Configures Redis broker, result backend, task context binding, and scheduled tasks.
Configured directly using Config to prevent circular imports with Flask application factory.
"""
import os
from celery import Celery, Task
from celery.schedules import crontab
from backend.config import Config


class FlaskTask(Task):
    """
    Custom Celery Task that ensures a Flask application context
    is always active during task execution.
    """
    _flask_app = None

    def __call__(self, *args, **kwargs):
        from flask import current_app
        if current_app:
            return self.run(*args, **kwargs)

        if FlaskTask._flask_app is None:
            from backend.app import create_app
            FlaskTask._flask_app = create_app()

        with FlaskTask._flask_app.app_context():
            return self.run(*args, **kwargs)


def create_celery_app():
    """
    Initializes and configures the Celery application.
    """
    celery = Celery(
        "emp_portal_tasks",
        task_cls=FlaskTask,
        include=["backend.tasks.jobs"],
    )

    broker_url = os.environ.get("CELERY_BROKER_URL", Config.CELERY_BROKER_URL)
    result_backend = os.environ.get("CELERY_RESULT_BACKEND", Config.CELERY_RESULT_BACKEND)
    always_eager = os.environ.get("CELERY_TASK_ALWAYS_EAGER", "false").lower() in ("true", "1")

    celery.conf.update(
        broker_url=broker_url,
        result_backend=result_backend,
        task_always_eager=always_eager,
        task_store_eager_result=True,
        timezone="Asia/Kolkata",
        enable_utc=True,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        beat_schedule={
            "daily-examination-reminder": {
                "task": "backend.tasks.jobs.send_daily_reminders",
                "schedule": crontab(hour=8, minute=0),  # Daily at 8:00 AM IST
            },
            "monthly-admin-report": {
                "task": "backend.tasks.jobs.send_monthly_admin_report",
                "schedule": crontab(day_of_month=1, hour=9, minute=0),  # 1st of every month at 9:00 AM IST
            },
        },
    )

    return celery


celery_app = create_celery_app()
