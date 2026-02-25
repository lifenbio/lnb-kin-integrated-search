import os
from celery import Celery
from celery.schedules import crontab


os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "main.settings.prod",
)

app = Celery('main')

app.config_from_object(
    "django.conf:settings",
    namespace="CELERY",
)

app.autodiscover_tasks()
app.conf.timezone = 'Asia/Seoul'

app.conf.beat_schedule = {
    "integration-area-collection-job": {
        "task": "common.tasks.integration_area_collection",
        "schedule": crontab(minute=0, hour='0', day_of_week='mon-sun')
    }
}

# 태스크별 큐 분리
app.conf.task_routes = {
    'common.tasks.process_keyword_task': {'queue': 'keywords'},
    'common.tasks.save_results_and_check_completion': {'queue': 'aggregation'},
    'common.tasks.integration_area_collection': {'queue': 'default'},
}

# chord 결과 보관
app.conf.result_expires = 79200