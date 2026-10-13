"""Test settings: no external services required. The SQLite fallback is for tests only."""

import os
import tempfile
from pathlib import Path

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
# Uploaded CSV files of the tests go to a throwaway directory, never into the working tree.
IMPORT_UPLOAD_DIR = Path(tempfile.mkdtemp(prefix="abm-test-import-uploads-"))

# A throwaway Fernet key per test process (never a real secret). Tests that need specific keys
# override settings.INTEGRATION_ENCRYPTION_KEYS.
from cryptography.fernet import Fernet

INTEGRATION_ENCRYPTION_KEYS = [Fernet.generate_key().decode("ascii")]
