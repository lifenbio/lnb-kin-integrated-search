"""
리모트 개발 환경 설정입니다.
ALLOWED_HOSTS 필드를 수정 후 사용하시는걸 추천드립니다.
원격 서버에 맞게 데이터베이스 설정 또한 바꾸시면 됩니다.
"""

from main.settings.base import *

DEBUG = os.environ.get("DEBUG")
SECRET_KEY = os.environ.get("SECRET_KEY")

ALLOWED_HOSTS = ['*']

DATABASES = {
    "default": {
        "ENGINE": os.environ.get("SQL_ENGINE", "django.db.backends.sqlite3"),
        "NAME": os.environ.get("SQL_DATABASE", os.path.join(BASE_DIR, "db.sqlite3")),
        "USER": os.environ.get("SQL_USER", "user"),
        "PASSWORD": os.environ.get("SQL_PASSWORD", "password"),
        "HOST": os.environ.get("SQL_HOST", "localhost"),
        "PORT": os.environ.get("SQL_PORT", "5432"),
        "CONN_MAX_AGE": 600,
    }
}