"""Local development settings."""

from apps.core.logging import build_logging_config

from .base import *
from .base import LOG_LEVEL, REST_FRAMEWORK, env

DEBUG = env.bool("DEBUG", default=True)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "[::1]"])

REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}
# Human-readable logs locally; set LOG_JSON=true to preview the production format.
LOGGING = build_logging_config(json_logs=env.bool("LOG_JSON", default=False), level=LOG_LEVEL)
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
