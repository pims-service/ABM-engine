"""ASGI entrypoint. Defaults to production settings; override with DJANGO_SETTINGS_MODULE."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
application = get_asgi_application()
