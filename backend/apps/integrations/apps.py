from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.integrations"
    label = "integrations"

    def ready(self) -> None:
        # "Test connection" needs a tester per provider that has an adapter.
        from .llm_connection import register_testers

        register_testers()
