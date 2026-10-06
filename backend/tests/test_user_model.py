from __future__ import annotations

import uuid

import pytest
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client

from apps.accounts.models import User
from tests.factories import DEFAULT_PASSWORD, make_user

pytestmark = pytest.mark.django_db


def test_auth_user_model_is_the_custom_user():
    assert settings.AUTH_USER_MODEL == "accounts.User"
    assert get_user_model() is User


def test_create_user_defaults():
    user = User.objects.create_user(email="  New.User@Example.COM ", password="s3cret-pass-phrase")
    assert isinstance(user.pk, uuid.UUID)
    assert user.email == "new.user@example.com"
    assert user.is_active
    assert not user.is_staff
    assert not user.is_superuser
    assert user.created_at <= user.updated_at
    assert user.check_password("s3cret-pass-phrase")
    assert str(user) == "new.user@example.com"
    assert user.get_username() == user.email
    assert user.get_full_name() == user.email
    user.name = "New User"
    assert user.get_short_name() == user.get_full_name() == "New User"


def test_create_user_without_password_is_unusable():
    assert not User.objects.create_user(email="a@example.com").has_usable_password()


def test_create_superuser():
    admin = User.objects.create_superuser(email="root@example.com", password="s3cret-pass-phrase")
    assert admin.is_staff
    assert admin.is_superuser


def test_create_superuser_refuses_non_staff_flags():
    with pytest.raises(ValueError, match="is_staff"):
        User.objects.create_superuser(email="x@example.com", password="p", is_staff=False)
    with pytest.raises(ValueError, match="is_superuser"):
        User.objects.create_superuser(email="x@example.com", password="p", is_superuser=False)


@pytest.mark.parametrize("email", ["", "   "])
def test_email_is_required(email: str):
    with pytest.raises(ValueError, match="email"):
        User.objects.create_user(email=email, password="p")


def test_email_is_unique_case_insensitively():
    make_user(email="dup@example.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_user(email="DUP@example.com")


def test_database_rejects_uppercase_emails_even_when_save_is_bypassed():
    make_user(email="ok@example.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(email="ok@example.com").update(email="OK@Example.com")


def test_clean_normalizes_email():
    user = User(email="Clean@Example.com")
    user.clean()
    assert user.email == "clean@example.com"


def test_django_authenticate_uses_email_and_rejects_inactive():
    user = make_user(email="login@example.com")
    assert authenticate(username="Login@Example.com", password=DEFAULT_PASSWORD) == user
    assert authenticate(username="login@example.com", password="nope") is None
    user.is_active = False
    user.save()
    assert authenticate(username="login@example.com", password=DEFAULT_PASSWORD) is None


def test_admin_login_still_works_with_session_auth():
    admin = User.objects.create_superuser(email="root@example.com", password="s3cret-pass-phrase")
    client = Client()
    assert client.login(username=admin.email, password="s3cret-pass-phrase")
    assert client.get("/admin/").status_code == 200
    assert client.get("/admin/accounts/user/").status_code == 200


def test_user_table_uses_a_uuid_primary_key():
    pk = next(f for f in User._meta.fields if f.primary_key)
    assert pk.get_internal_type() == "UUIDField"
    assert "accounts_user" in connection.introspection.table_names()


@pytest.mark.parametrize(
    "password",
    ["short1!A", "password1234", "123456789012345"],
)
def test_weak_passwords_are_rejected(password: str):
    with pytest.raises(ValidationError):
        validate_password(password)


def test_password_similar_to_email_is_rejected():
    user = User(email="khuzaim.khan@example.com")
    with pytest.raises(ValidationError):
        validate_password("khuzaim.khan", user)


def test_strong_password_is_accepted():
    validate_password("correct-horse-battery-staple")
