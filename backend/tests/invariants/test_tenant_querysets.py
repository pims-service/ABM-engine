"""``for_user`` and ``for_client`` never leak another client's rows (every tenant queryset)."""

from __future__ import annotations

import pytest

from apps.campaigns.memberships import grant_membership, revoke_membership
from apps.campaigns.models import Client
from tests.factories import make_user
from tests.invariants.registry import has_tenant_queryset, label, project_models

MODELS = [m for m in project_models() if has_tenant_queryset(m)]


def ids(model, rows: dict) -> set:
    return set(model.objects.filter(pk=rows[label(model)].pk).values_list("pk", flat=True))


def visible(model, user) -> set:
    return set(model.objects.for_user(user).values_list("pk", flat=True))


@pytest.fixture
def users(two_clients):
    a_user = make_user()
    grant_membership(two_clients.a, a_user, "viewer")
    b_user = make_user()
    grant_membership(two_clients.b, b_user, "admin")
    return {
        "a": a_user,
        "b": b_user,
        "nobody": make_user(),
        "admin": make_user(is_superuser=True),
        "inactive_admin": make_user(is_superuser=True, is_active=False),
        "inactive_member": make_user(is_active=False),
    }


@pytest.mark.parametrize("model", MODELS, ids=label)
def test_for_user_returns_only_the_users_client(model, two_clients, users) -> None:
    key = label(model)
    row_a, row_b = two_clients.rows_a[key], two_clients.rows_b[key]
    seen = visible(model, users["a"])
    assert row_a.pk in seen
    assert row_b.pk not in seen
    seen_b = visible(model, users["b"])
    assert row_b.pk in seen_b
    assert row_a.pk not in seen_b


@pytest.mark.parametrize("model", MODELS, ids=label)
def test_no_membership_anonymous_and_inactive_users_see_nothing(model, two_clients, users) -> None:
    from django.contrib.auth.models import AnonymousUser

    assert visible(model, users["nobody"]) == set()
    assert visible(model, AnonymousUser()) == set()
    assert visible(model, None) == set()
    assert visible(model, users["inactive_admin"]) == set()
    grant_membership(two_clients.a, users["inactive_member"], "admin")
    assert visible(model, users["inactive_member"]) == set()


@pytest.mark.parametrize("model", MODELS, ids=label)
def test_global_admin_sees_every_clients_rows(model, two_clients, users) -> None:
    key = label(model)
    seen = visible(model, users["admin"])
    assert {two_clients.rows_a[key].pk, two_clients.rows_b[key].pk} <= seen


@pytest.mark.parametrize("model", MODELS, ids=label)
def test_for_client_scopes_to_one_client(model, two_clients) -> None:
    key = label(model)
    a_rows = set(model.objects.for_client(two_clients.a).values_list("pk", flat=True))
    by_id = set(model.objects.for_client(two_clients.a.pk).values_list("pk", flat=True))
    assert a_rows == by_id
    assert two_clients.rows_a[key].pk in a_rows
    assert two_clients.rows_b[key].pk not in a_rows


@pytest.mark.parametrize("model", MODELS, ids=label)
def test_a_revoked_membership_stops_seeing_rows_at_once(model, two_clients, users) -> None:
    key = label(model)
    assert two_clients.rows_a[key].pk in visible(model, users["a"])
    revoke_membership(two_clients.a, users["a"])
    assert visible(model, users["a"]) == set()


@pytest.mark.parametrize("model", MODELS, ids=label)
def test_a_member_of_both_clients_sees_both_and_nothing_else(model, two_clients, users) -> None:
    key = label(model)
    grant_membership(two_clients.b, users["a"], "viewer")
    third = Client.objects.create(name="Invariant C")
    seen = visible(model, users["a"])
    assert {two_clients.rows_a[key].pk, two_clients.rows_b[key].pk} <= seen
    assert not seen & set(model.objects.for_client(third).values_list("pk", flat=True))
