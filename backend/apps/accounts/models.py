"""Custom user model: email is the login, primary key is a UUID."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models.functions import Lower


class UserManager(BaseUserManager["User"]):
    """Creates users by email. Emails are stored lower-cased so lookups are case-insensitive."""

    use_in_migrations = True

    @staticmethod
    def normalize(email: str) -> str:
        return email.strip().lower()

    def _create_user(self, email: str, password: str | None, **extra: Any) -> User:
        if not email or not email.strip():
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize(email), **extra)
        user.set_password(password)  # None stores an unusable password
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra: Any) -> User:
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra: Any) -> User:
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if extra["is_staff"] is not True or extra["is_superuser"] is not True:
            raise ValueError("A superuser must have is_staff=True and is_superuser=True.")
        return self._create_user(email, password, **extra)

    def get_by_natural_key(self, username: str | None) -> User:
        return self.get(email=self.normalize(username or ""))


class User(AbstractBaseUser, PermissionsMixin):
    """A person who can sign in. Accounts are created by admins; there is no self-signup.

    Roles and per-client access are added with the tenancy model (issue #46).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField("email address", max_length=254, unique=True)
    name = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(
        default=True, help_text="Inactive users cannot log in or refresh tokens."
    )
    is_staff = models.BooleanField(default=False, help_text="Can sign in to the Django admin.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    class Meta:
        ordering: ClassVar[tuple[str, ...]] = ("email",)
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(email=Lower("email")), name="accounts_user_email_lowercase"
            )
        ]

    def __str__(self) -> str:
        return self.email

    def clean(self) -> None:
        super().clean()
        self.email = UserManager.normalize(self.email)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.email = UserManager.normalize(self.email)
        super().save(*args, **kwargs)

    def get_full_name(self) -> str:
        return self.name or self.email

    def get_short_name(self) -> str:
        return self.name or self.email
