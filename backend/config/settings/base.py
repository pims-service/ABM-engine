"""Base settings shared by every environment. All config comes from environment variables."""

from datetime import timedelta
from pathlib import Path

import environ

from apps.core.logging import build_logging_config

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# Optional local .env file (never committed). Real environment variables win.
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

# Required: no default, so a missing value fails loudly at startup.
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
    "rest_framework_simplejwt.token_blacklist",
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
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"  # must be set before the first migration of any dependent app

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
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
    # JWT only (no session auth: the admin uses Django's own session login, not DRF).
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
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
        # Login attempts (see apps/accounts/throttles.py): per client IP and per email.
        "login": env("API_THROTTLE_LOGIN", default="20/min"),
        "login_email": env("API_THROTTLE_LOGIN_EMAIL", default="5/min"),
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
    "workers": env.int("Q_WORKERS", default=2),
    "timeout": env.int("Q_TASK_TIMEOUT", default=300),  # hard limit per task, seconds
    "retry": env.int("Q_TASK_RETRY", default=360),  # must exceed timeout
    "max_attempts": 3,  # redeliveries of a task whose worker died before acking
    "ack_failures": True,
    "bulk": 1,
    "save_limit": 500,  # finished task rows kept for the admin; older ones are pruned
    "catch_up": False,  # do not replay missed schedules after downtime
}

# Authentication (ADR 0006): SimpleJWT. Short-lived access token; refresh token rotates on every
# use and the old one is blacklisted, so a reused (stolen) refresh token is rejected.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_LIFETIME_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_LIFETIME_DAYS", default=7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}
# Optional: deliver the refresh token as an httpOnly cookie instead of in the JSON body, for the
# Next.js BFF (apps/accounts/cookies.py). The body-based flow keeps working for API clients.
AUTH_REFRESH_COOKIE_ENABLED = env.bool("AUTH_REFRESH_COOKIE_ENABLED", default=False)
AUTH_REFRESH_COOKIE_NAME = env("AUTH_REFRESH_COOKIE_NAME", default="abm_refresh")
AUTH_REFRESH_COOKIE_PATH = "/api/v1/auth/"  # only sent to the auth endpoints
AUTH_REFRESH_COOKIE_SECURE = env.bool("AUTH_REFRESH_COOKIE_SECURE", default=True)
AUTH_REFRESH_COOKIE_SAMESITE = env("AUTH_REFRESH_COOKIE_SAMESITE", default="Lax")
