"""The Django admin (issue #54): every model is there or explicitly excluded, history is read
only, only staff get in, sensitive values are masked, and changelists do not run N+1 queries.

The rules behind these tests are written down in ``docs/admin.md``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from django.apps import apps
from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Group, Permission
from django.db import connection
from django.test import Client as DjangoClient
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import User
from apps.campaigns.models import ClientMembership
from apps.core.admin_base import ADMIN_EXCLUDED_MODELS, mask_email
from apps.core.base import AppendOnlyModel
from apps.core.models import AuditLog, Job, JobItem
from apps.outreach.services import record_activity
from tests.factories import (
    make_ai_recommendation,
    make_angle,
    make_campaign,
    make_client,
    make_company,
    make_contact,
    make_data_source,
    make_human_decision,
    make_icp_assessment,
    make_message,
    make_research,
    make_signal,
    make_user,
)
from tests.factories_core import make_audit_log, make_job, make_job_item

pytestmark = pytest.mark.django_db


def _admin_url(model: Any, kind: str, *args: Any) -> str:
    meta = model._meta
    return reverse(f"admin:{meta.app_label}_{meta.model_name}_{kind}", args=args)


def _superuser() -> User:
    return make_user(is_staff=True, is_superuser=True)


def _staff(*codenames: str) -> User:
    """A staff user who is not a superuser, holding only the named model permissions."""
    user = make_user(is_staff=True)
    user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    return user


def _logged_in(user: User) -> DjangoClient:
    http = DjangoClient()
    http.force_login(user)
    return http


# ------------------------------------------------------------------ registry


def test_every_concrete_model_is_registered_or_excluded_with_a_reason() -> None:
    concrete = {m._meta.label: m for m in apps.get_models()}
    missing = sorted(
        label
        for label, model in concrete.items()
        if not admin.site.is_registered(model) and label not in ADMIN_EXCLUDED_MODELS
    )
    assert not missing, (
        f"Register these models in their app's admin.py (read only if they are history), or add "
        f"them to ADMIN_EXCLUDED_MODELS with a reason: {missing}"
    )


def test_exclusion_list_has_no_stale_or_registered_entries() -> None:
    labels = {m._meta.label for m in apps.get_models()}
    for label, reason in ADMIN_EXCLUDED_MODELS.items():
        assert label in labels, f"{label} no longer exists: remove it from ADMIN_EXCLUDED_MODELS"
        model = apps.get_model(label)
        assert not admin.site.is_registered(model), f"{label} is registered; drop the exclusion"
        assert reason.strip(), f"{label} needs a reason"


def _read_only_models() -> list[Any]:
    append_only = [m for m in apps.get_models() if issubclass(m, AppendOnlyModel)]
    extra = [Job, JobItem, LogEntry, OutstandingToken, BlacklistedToken]
    return [*append_only, *extra]


@pytest.mark.parametrize("model", _read_only_models(), ids=lambda m: m._meta.label)
def test_history_models_are_read_only_in_admin(model: Any, rf: Any) -> None:
    modeladmin = admin.site._registry[model]
    request = rf.get("/")
    request.user = _superuser()
    assert modeladmin.has_add_permission(request) is False
    assert modeladmin.has_change_permission(request) is False
    assert modeladmin.has_delete_permission(request) is False
    # No stock bulk action can sneak a delete or edit back in.
    assert "delete_selected" not in modeladmin.get_actions(request)
    # Whatever the page displays is read only (a field left out, like a token string or a raw
    # diff, is not displayed at all).
    shown = set(modeladmin.get_fields(request))
    assert shown <= set(modeladmin.get_readonly_fields(request))


def test_all_append_only_models_are_found() -> None:
    labels = {m._meta.label for m in _read_only_models()}
    assert {"campaigns.CampaignProfile", "research.Signal", "outreach.MessageSource"} <= labels
    assert "core.AuditLog" in labels


def test_posts_to_read_only_admin_pages_are_rejected() -> None:
    http = _logged_in(_superuser())
    signal = make_signal()
    log = make_audit_log()
    profile = make_campaign().current_profile
    for obj in (signal, log, profile):
        model: Any = type(obj)
        assert http.post(_admin_url(model, "add"), {}).status_code == 403
        assert http.post(_admin_url(model, "change", obj.pk), {}).status_code == 403
        assert http.post(_admin_url(model, "delete", obj.pk), {"post": "yes"}).status_code == 403
        assert model.objects.filter(pk=obj.pk).exists()
    signal.refresh_from_db()
    assert signal.evidence == "Raised a Series B round."


def test_bulk_delete_action_is_not_offered_on_history_changelists() -> None:
    signal = make_signal()
    http = _logged_in(_superuser())
    response = http.post(
        _admin_url(type(signal), "changelist"),
        {"action": "delete_selected", "_selected_action": [str(signal.pk)], "post": "yes"},
    )
    assert response.status_code in (200, 302)
    assert type(signal).objects.filter(pk=signal.pk).exists()


def test_company_and_contact_admin_cannot_edit_or_delete() -> None:
    http = _logged_in(_superuser())
    contact = make_contact()
    company = contact.company
    for obj in (company, contact):
        model = type(obj)
        assert http.post(_admin_url(model, "add"), {}).status_code == 403
        assert http.post(_admin_url(model, "change", obj.pk), {"name": "x"}).status_code == 403
        assert http.post(_admin_url(model, "delete", obj.pk), {"post": "yes"}).status_code == 403
    company.refresh_from_db()
    assert company.name != "x"


# ------------------------------------------------------------------ access


def test_anonymous_and_non_staff_cannot_reach_the_admin() -> None:
    plain = make_user()  # active, not staff
    client_role_admin = make_user()
    ClientMembership.objects.create(user=client_role_admin, client=make_client(), role="admin")
    superuser_not_staff = make_user(is_superuser=True, is_staff=False)
    inactive_staff = make_user(is_staff=True, is_superuser=True, is_active=False)
    urls = ["/admin/", _admin_url(Job, "changelist"), _admin_url(AuditLog, "changelist")]

    for user in (None, plain, client_role_admin, superuser_not_staff, inactive_staff):
        http = DjangoClient()
        if user is not None and user.is_active:
            http.force_login(user)
        for url in urls:
            response = http.get(url)
            assert response.status_code == 302, (user, url)
            assert response["Location"].startswith("/admin/login/"), (user, url)


def test_admin_login_form_rejects_a_non_staff_user() -> None:
    plain = make_user()
    response = DjangoClient().post(
        "/admin/login/",
        {"username": plain.email, "password": "test-password-123"},  # pragma: allowlist secret
    )
    assert response.status_code == 200  # form shown again, no session started
    assert "_auth_user_id" not in response.wsgi_request.session


def test_staff_without_model_permissions_sees_no_models() -> None:
    http = _logged_in(make_user(is_staff=True))
    assert http.get("/admin/").status_code == 200
    assert http.get(_admin_url(Job, "changelist")).status_code == 403
    assert http.get(_admin_url(AuditLog, "changelist")).status_code == 403


def test_staff_with_a_view_permission_can_view_only_that_model() -> None:
    http = _logged_in(_staff("view_job"))
    assert http.get(_admin_url(Job, "changelist")).status_code == 200
    assert http.get(_admin_url(AuditLog, "changelist")).status_code == 403
    assert http.get(_admin_url(Job, "add")).status_code == 403


@pytest.mark.parametrize(
    "model",
    [User, Group, ClientMembership, OutstandingToken, BlacklistedToken, LogEntry],
    ids=lambda m: m._meta.label,
)
def test_superuser_only_models_stay_hidden_from_privileged_staff(model: Any) -> None:
    """Even a staff member who holds every permission on the model gets nothing."""
    meta = model._meta
    codenames = [f"{p}_{meta.model_name}" for p in ("view", "add", "change", "delete")]
    http = _logged_in(_staff(*codenames))
    assert http.get(_admin_url(model, "changelist")).status_code == 403
    assert http.get(_admin_url(model, "add")).status_code == 403
    assert meta.label not in {
        f"{m['app_label']}.{m['object_name']}"
        for app in http.get("/admin/").context["app_list"]
        for m in app["models"]
    }
    assert _logged_in(_superuser()).get(_admin_url(model, "changelist")).status_code == 200


def test_staff_cannot_grant_themselves_superuser() -> None:
    staff = _staff("view_user", "change_user")
    http = _logged_in(staff)
    response = http.post(_admin_url(User, "change", staff.pk), {"is_superuser": "on"})
    assert response.status_code == 403
    staff.refresh_from_db()
    assert not staff.is_superuser


# ------------------------------------------------------------------ sensitive fields


def test_user_page_never_shows_the_password_hash() -> None:
    user = make_user()
    assert user.password.startswith(("pbkdf2_", "md5$"))
    http = _logged_in(_superuser())
    page = http.get(_admin_url(User, "change", user.pk)).content.decode()
    hash_parts = [p for p in user.password.replace("$", " ").split() if len(p) > 8]
    assert "hash hidden" in page
    assert user.password not in page
    assert all(part not in page for part in hash_parts)
    assert "algorithm" not in page.lower()
    assert "password" in http.get(_admin_url(User, "changelist")).content.decode().lower()
    # The stock change-password form is still the way to set one.
    assert http.get(reverse("admin:auth_user_password_change", args=[user.pk])).status_code == 200


def test_unusable_password_is_reported_without_a_hash() -> None:
    user = make_user()
    user.set_unusable_password()
    user.save()
    page = _logged_in(_superuser()).get(_admin_url(User, "change", user.pk)).content.decode()
    assert "Not set" in page


def test_refresh_token_strings_are_not_displayed() -> None:
    user = make_user()
    refresh = RefreshToken.for_user(user)
    token = OutstandingToken.objects.get(jti=refresh["jti"])
    refresh.blacklist()
    http = _logged_in(_superuser())
    for page in (
        http.get(_admin_url(OutstandingToken, "change", token.pk)).content.decode(),
        http.get(_admin_url(OutstandingToken, "changelist")).content.decode(),
        http.get(_admin_url(BlacklistedToken, "changelist")).content.decode(),
    ):
        assert str(refresh) not in page
        assert token.token not in page
    assert token.jti in http.get(_admin_url(OutstandingToken, "changelist")).content.decode()
    blacklisted = BlacklistedToken.objects.get(token=token)
    delete_url = _admin_url(BlacklistedToken, "delete", blacklisted.pk)
    assert http.post(delete_url, {"post": "yes"}).status_code == 403
    assert BlacklistedToken.objects.filter(pk=blacklisted.pk).exists()


def test_mask_email() -> None:
    assert mask_email("jane.doe@example.com") == "j***@example.com"
    assert mask_email("") == ""
    assert mask_email("not-an-email") == "***"


def _contact_with_pii() -> Any:
    return make_contact(
        name="Jane Doe",
        email="jane.doe@acme.example",
        email_status="unverified",
        profile_url="https://linkedin.example/in/jane-doe",
    )


def test_contact_email_and_profile_are_shown_only_to_superusers() -> None:
    contact = _contact_with_pii()
    detail = _admin_url(type(contact), "change", contact.pk)
    listing = _admin_url(type(contact), "changelist")

    superuser_page = _logged_in(_superuser()).get(detail).content.decode()
    assert "jane.doe@acme.example" in superuser_page
    assert "linkedin.example/in/jane-doe" in superuser_page

    staff = _logged_in(_staff("view_contact"))
    for page in (staff.get(detail).content.decode(), staff.get(listing).content.decode()):
        assert "jane.doe@acme.example" not in page
        assert "linkedin.example" not in page
        assert "Jane Doe" in page
    assert "j***@acme.example" in staff.get(detail).content.decode()
    assert "(hidden)" in staff.get(detail).content.decode()


def test_staff_cannot_search_contacts_by_email_but_superusers_can() -> None:
    contact = _contact_with_pii()
    listing = _admin_url(type(contact), "changelist")
    staff = _logged_in(_staff("view_contact"))
    found_by_staff = staff.get(listing, {"q": "jane.doe@acme.example"}).content.decode()
    assert "Jane Doe" not in found_by_staff
    found_by_super = _logged_in(_superuser()).get(listing, {"q": "jane.doe@acme.example"})
    assert "Jane Doe" in found_by_super.content.decode()


def test_audit_log_diffs_are_redacted_on_display() -> None:
    nested = {"refresh_token": "tok-999"}  # pragma: allowlist secret
    entry = AuditLog.objects.create(
        action="update",
        object_type="user",
        object_id=uuid.uuid4(),
        before={"password": "hunter2-old", "name": "Visible Name"},  # pragma: allowlist secret
        after={"api_key": "sk-live-abc123", "nested": {"x": nested}},  # pragma: allowlist secret
    )
    page = _logged_in(_superuser()).get(_admin_url(AuditLog, "change", entry.pk)).content.decode()
    for secret in ("hunter2-old", "sk-live-abc123", "tok-999"):
        assert secret not in page
    assert "[REDACTED]" in page
    assert "Visible Name" in page
    # The stored row is untouched (append-only): only the display is redacted.
    entry.refresh_from_db()
    assert (entry.before or {})["password"] == "hunter2-old"  # pragma: allowlist secret


def test_audit_log_written_by_the_service_is_redacted_at_rest() -> None:
    entry = make_audit_log(
        action="update",
        before={"password": "old-pw"},  # pragma: allowlist secret
        after={"password": "new-pw"},  # pragma: allowlist secret
    )
    assert entry.before == {"password": "[REDACTED]"}
    assert entry.after == {"password": "[REDACTED]"}


# ------------------------------------------------------------------ N+1


def _evidence_link() -> Any:
    company = make_company()
    return make_angle(company=company, signals=[make_signal(company=company)])


def _members() -> Any:
    return ClientMembership.objects.create(user=make_user(), client=make_client(), role="viewer")


def _activity() -> Any:
    return record_activity(make_company(), "researched")


def _research_chain() -> Any:
    return make_ai_recommendation()


def _decision() -> Any:
    return make_human_decision(company=make_company())


def _job_item() -> Any:
    return make_job_item(job=make_job())


CHANGELISTS: dict[str, tuple[str, Callable[[], Any]]] = {
    "client": ("campaigns_client", make_client),
    "campaign": ("campaigns_campaign", make_campaign),
    "campaign_profile": ("campaigns_campaignprofile", make_campaign),
    "membership": ("campaigns_clientmembership", _members),
    "company": ("companies_company", make_company),
    "research": ("companies_companyresearch", make_research),
    "contact": ("companies_contact", make_contact),
    "data_source": ("companies_datasource", make_data_source),
    "signal": ("research_signal", make_signal),
    "icp_assessment": ("research_icpassessment", make_icp_assessment),
    "ai_recommendation": ("research_airecommendation", _research_chain),
    "human_decision": ("research_humandecision", _decision),
    "angle": ("outreach_outreachangle", make_angle),
    "angle_signal": ("outreach_anglesignal", _evidence_link),
    "message": ("outreach_message", make_message),
    "activity": ("outreach_activity", _activity),
    "job": ("core_job", make_job),
    "job_item": ("core_jobitem", _job_item),
    "audit_log": ("core_auditlog", make_audit_log),
    "user": ("accounts_user", make_user),
}


def _count_queries(http: DjangoClient, url: str) -> int:
    with CaptureQueriesContext(connection) as ctx:
        response = http.get(url)
    assert response.status_code == 200
    return len(ctx)


@pytest.mark.parametrize("name", sorted(CHANGELISTS))
def test_changelist_query_count_does_not_grow_with_rows(name: str) -> None:
    """Three more rows must not add queries (no N+1 through the columns we display)."""
    url_name, make = CHANGELISTS[name]
    url = reverse(f"admin:{url_name}_changelist")
    http = _logged_in(_superuser())
    make()
    http.get(url)  # warm caches (content types, session)
    baseline = _count_queries(http, url)
    for _ in range(3):
        make()
    assert _count_queries(http, url) == baseline, f"{url_name} changelist grows with rows"
