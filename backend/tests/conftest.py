"""Shared pytest fixtures. Database access comes from pytest-django (`db`, `transactional_db`)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from tests.factories import make_user


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    """Throttle counters live in the cache; never let them leak between tests."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _no_real_website_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The website research endpoint must never reach the network from a test: by default it
    sees an unreachable site. Tests of it patch ``fetch_site_pages`` again."""
    from apps.integrations.fetch import FetchNetworkError

    def unreachable(*args: object, **kwargs: object) -> object:
        raise FetchNetworkError(reason="network_error")

    monkeypatch.setattr("apps.companies.api.website_research.fetch_site_pages", unreachable)


@pytest.fixture(autouse=True)
def _fake_provider_registered() -> Iterator[None]:
    """A fresh ``fake`` provider in the default registry for every test (other tests may clear
    the registry); put the registry back as it was afterwards."""
    from django.conf import settings

    from apps.imports.providers import default_registry
    from apps.imports.providers.dev import register_dev_providers

    saved = default_registry.list()
    if settings.PROVIDER_FAKE_ENABLED:
        register_dev_providers(replace=True)
    yield
    default_registry.clear()
    for provider in saved:
        default_registry.register(provider)


@pytest.fixture
def api_client() -> APIClient:
    """Unauthenticated DRF test client."""
    return APIClient()


@pytest.fixture
def user(db: None) -> User:
    """A persisted, active user (password: `tests.factories.DEFAULT_PASSWORD`)."""
    return make_user()


@pytest.fixture
def auth_client(api_client: APIClient, user: User) -> APIClient:
    """API client sending a real JWT access token for `user`."""
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return api_client


from tests.fixtures_seed import seeded_world  # noqa: E402,F401  (re-exported fixture, issue #52)
