from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.integrations"
    label = "integrations"

    def ready(self) -> None:
        # "Test connection" needs a tester per provider that has an adapter.
        from . import crm_connection, llm_connection, people_connection

        llm_connection.register_testers()
        crm_connection.register_testers()
        people_connection.register_testers()
