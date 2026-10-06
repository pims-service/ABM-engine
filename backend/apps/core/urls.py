from django.urls import path

from .views import ApiRootView

app_name = "v1"

urlpatterns = [
    path("", ApiRootView.as_view(), name="api-root"),
]
