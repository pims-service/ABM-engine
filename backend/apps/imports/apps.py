from django.apps import AppConfig
from django.conf import settings


class ImportsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.imports"
    label = "imports"

    def ready(self) -> None:
        # The fake provider is a dev/test fixture: registered only when the setting allows it
        # (PROVIDER_FAKE_ENABLED, default on in dev and test settings, off elsewhere).
        if getattr(settings, "PROVIDER_FAKE_ENABLED", False):
            from .providers.dev import register_dev_providers

            register_dev_providers()
