"""factory_boy factories. Add one per model; import them from here in tests and fixtures."""

from __future__ import annotations

from typing import Any, cast

import factory
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User

DEFAULT_PASSWORD = "test-password"


class UserFactory(factory.django.DjangoModelFactory):
    """Placeholder factory for the stock auth user (replaced by the custom user model later)."""

    class Meta:
        model = User

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")
    # Hashed at build time (test settings use a fast hasher), so no post-generation save needed.
    password = factory.LazyFunction(lambda: make_password(DEFAULT_PASSWORD))


def make_user(**overrides: Any) -> User:
    """Create a persisted user. factory_boy is untyped, so this gives callers a real `User`."""
    return cast(User, UserFactory(**overrides))
