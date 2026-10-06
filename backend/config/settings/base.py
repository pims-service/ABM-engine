"""Base settings shared by every environment. All config comes from environment variables."""

import os
from pathlib import Path

import environ

from apps.core.logging import build_logging_config
from config.env_validation import check_environment

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# Optional local .env file (never committed). Real environment variables win.
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

# Fail fast, naming every missing or invalid variable (never its value). Test settings supply
# throwaway values and skip this check.
_SETTINGS_MODULE = os.environ.get("DJANGO_SETTINGS_MODULE", "")
if not _SETTINGS_MODULE.endswith(".test"):
    check_environment(os.environ, settings_module=_SETTINGS_MODULE)

# Required and validated above.
SECRET_KEY = env("SECRET_KEY")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]
THIRD_PARTY_APPS = [
    "rest_framework",
    "django_q",
]
LOCAL_APPS = [
    "apps.accounts.apps.AccountsConfig",
    "apps.campaigns.apps.CampaignsConfig",
    "apps.companies.apps.CompaniesConfig",
    "apps.research.apps.ResearchConfig",
    "apps.integrations.apps.IntegrationsConfig",
    "apps.ai.apps.AIConfig",
    "apps.core.apps.CoreConfig",
]
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "apps.core.middleware.RequestIDMiddleware",  # first: every response gets X-Request-ID
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# PostgreSQL via DATABASE_URL, e.g. postgres://USER:PASSWORD@HOST:5432/DBNAME
DATABASES = {"default": env.db("DATABASE_URL")}
if DATABASES["default"]["ENGINE"].endswith("postgresql"):
    # Fail fast instead of hanging when the database host is unreachable (health checks, workers).
    DATABASES["default"].setdefault("OPTIONS", {}).setdefault("connect_timeout", 3)

# Django-Q2 publishes worker heartbeats (cluster stats) to a cache, and /readyz reads them from
# the web process. The default cache is per-process, so use a database cache shared by api and
# worker. Its table is created by apps/core/migrations/0003_q_stats_cache_table.py.
Q_STATS_CACHE_TABLE = "q_stats_cache"
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "q_stats": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": Q_STATS_CACHE_TABLE,
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Django REST Framework
REST_FRAMEWORK = {
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.NamespaceVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ["v1"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    # JWT auth is added in a separate issue; session auth is a placeholder.
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.DefaultPagination",
    "PAGE_SIZE": env.int("API_PAGE_SIZE", default=25),
    # Throttling stubs: classes enabled, generous default rates (tune per env).
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("API_THROTTLE_ANON", default="100/hour"),
        "user": env("API_THROTTLE_USER", default="1000/hour"),
    },
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
}

# Logging: JSON lines by default (prod); dev.py switches to a readable format.
LOG_LEVEL = env("LOG_LEVEL", default="INFO")
LOGGING = build_logging_config(json_logs=env.bool("LOG_JSON", default=True), level=LOG_LEVEL)

# Background jobs: Django-Q2 with the Django ORM broker, i.e. the queue lives in Postgres
# (ADR 0005; no Redis, no Celery). Run the worker with `python manage.py qcluster`.
# Tasks are acked only after they finish (a crashed worker's task is redelivered after `retry`
# seconds). `ack_failures` is on because retries with backoff are handled by apps.core.jobs.
# Payloads must be JSON values; apps.core.jobs enforces that when enqueuing.
Q_CLUSTER = {
    "name": "abm",
    "orm": "default",
    "cache": "q_stats",  # cluster heartbeats, read by /readyz (apps/core/health.py)
    "workers": env.int("Q_WORKERS", default=2),
    "timeout": env.int("Q_TASK_TIMEOUT", default=300),  # hard limit per task, seconds
    "retry": env.int("Q_TASK_RETRY", default=360),  # must exceed timeout
    "max_attempts": 3,  # redeliveries of a task whose worker died before acking
    "ack_failures": True,
    "bulk": 1,
    "save_limit": 500,  # finished task rows kept for the admin; older ones are pruned
    "catch_up": False,  # do not replay missed schedules after downtime
}
