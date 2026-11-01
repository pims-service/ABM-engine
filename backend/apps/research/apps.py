from django.apps import AppConfig


class ResearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.research"
    label = "research"

    def ready(self) -> None:
        # Registers the research job type so a worker can resume a run after a restart.
        from . import tasks  # noqa: F401
