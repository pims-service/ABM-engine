"""Shared pytest fixtures. Database access comes from pytest-django (`db`, `transactional_db`)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from tests.factories import make_user


@pytest.fixture
def api_client() -> APIClient:
    """Unauthenticated DRF test client."""
    return APIClient()


@pytest.fixture
def user(db: None) -> User:
    """A persisted user. Placeholder until the custom user model lands in `accounts`."""
    return make_user()


@pytest.fixture
def auth_client(api_client: APIClient, user: User) -> APIClient:
    """API client authenticated as `user`.

    Uses `force_authenticate`, which bypasses the authentication classes. Swap this for a
    real JWT header once SimpleJWT is wired up; tests depending on this fixture won't change.
    """
    api_client.force_authenticate(user=user)
    return api_client
