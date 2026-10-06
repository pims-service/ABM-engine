from django.urls import path

from .views import LoginView, LogoutView, MeView, RefreshView

# Included by apps.core.urls under /api/v1/auth/, so reverse as "v1:auth-login" etc.
urlpatterns = [
    path("login/", LoginView.as_view(), name="auth-login"),
    path("refresh/", RefreshView.as_view(), name="auth-refresh"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
    path("me/", MeView.as_view(), name="auth-me"),
]
