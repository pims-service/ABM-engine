"""factory_boy factories. Add one per model; import them from here in tests and fixtures."""

from __future__ import annotations

from typing import Any, cast

import factory
from django.contrib.auth.hashers import make_password

from apps.accounts.models import User

DEFAULT_PASSWORD = "test-password-123"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    name = factory.Sequence(lambda n: f"User {n}")
    # Hashed at build time (test settings use a fast hasher), so no post-generation save needed.
    password = factory.LazyFunction(lambda: make_password(DEFAULT_PASSWORD))


def make_user(**overrides: Any) -> User:
    """Create a persisted user. factory_boy is untyped, so this gives callers a real `User`."""
    return cast(User, UserFactory(**overrides))
