"""Example: model layer. Hits the test database via the `db` fixture and a factory."""

import pytest

from tests.factories import make_user

pytestmark = pytest.mark.django_db


def test_user_factory_persists_and_hashes_password():
    user = make_user(username="ada")
    user.refresh_from_db()
    assert user.pk is not None
    assert user.check_password("test-password")
    assert str(user) == "ada"
