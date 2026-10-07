"""Production settings. Secure defaults; everything overridable via env vars."""

from .base import *
from .base import env

DEBUG = False
# ALLOWED_HOSTS must be provided explicitly (comma-separated) in production.
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
# Container/orchestrator probes speak plain HTTP to the pod; never redirect them to https.
SECURE_REDIRECT_EXEMPT = [r"^healthz$", r"^readyz$"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=3600)
SECURE_CONTENT_TYPE_NOSNIFF = True
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
# Explicit frontend origins (comma-separated, no wildcard). No dev default in production.
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS")
