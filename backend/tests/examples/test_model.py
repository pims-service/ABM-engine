"""Example: model layer. Hits the test database via the `db` fixture and a factory."""

import pytest

from tests.factories import DEFAULT_PASSWORD, make_user

pytestmark = pytest.mark.django_db


def test_user_factory_persists_and_hashes_password():
    user = make_user(email="Ada@Example.com", name="Ada")
    user.refresh_from_db()
    assert user.pk is not None
    assert user.check_password(DEFAULT_PASSWORD)
    assert str(user) == "ada@example.com"
