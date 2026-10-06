"""Test settings: no external services required. The SQLite fallback is for tests only."""

import os

# Throwaway test values (not real secrets) so tests run without a .env file.
os.environ.setdefault("SECRET_KEY", "test-only-insecure-key-not-a-secret")
os.environ.setdefault("DATABASE_URL", "sqlite://:memory:")

from .base import *
from .base import Q_CLUSTER, REST_FRAMEWORK

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
REST_FRAMEWORK = {**REST_FRAMEWORK, "DEFAULT_THROTTLE_CLASSES": []}
# Run queued tasks inline in the calling process, so tests need no worker or broker.
Q_CLUSTER = {**Q_CLUSTER, "sync": True}
