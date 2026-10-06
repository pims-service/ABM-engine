from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"

    def ready(self) -> None:
        # Import every app's `tasks` module so each @tracked_job registers its job type before a
        # job is created (the worker process imports them by path anyway).
        from django.utils.module_loading import autodiscover_modules

        autodiscover_modules("tasks")
