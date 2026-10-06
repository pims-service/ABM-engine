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
