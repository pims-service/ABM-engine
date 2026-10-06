"""Contact model: roles, one primary rule, archive, erase, tenancy (issue #41)."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.admin.sites import site
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.companies import services
from apps.companies.models import Contact, ContactRole, EmailStatus
from apps.core.base import TenantMismatchError
from tests.factories import make_company, make_contact, make_data_source

pytestmark = pytest.mark.django_db


def _contact(company, **kw):
    return make_contact(company=company, **kw)


class TestConstraints:
    def test_defaults(self):
        contact = _contact(make_company())
        assert contact.role == ContactRole.NONE
        assert contact.email_status == EmailStatus.UNKNOWN
        assert contact.client_id == contact.company.client_id

    def test_roles_and_statuses_limited(self):
        assert set(ContactRole.values) == {"primary", "secondary", "none"}
        assert set(EmailStatus.values) == {
            "unknown",
            "not_found",
            "unverified",
            "verified",
            "invalid",
        }
        company = make_company()
        with pytest.raises(IntegrityError), transaction.atomic():
            _contact(company, role="boss")
        with pytest.raises(IntegrityError), transaction.atomic():
            _contact(company, email_status="great")

    def test_one_primary_and_one_secondary_per_company(self):
        company = make_company()
        _contact(company, role="primary")
        with pytest.raises(IntegrityError), transaction.atomic():
            _contact(company, role="primary")
        _contact(company, role="secondary")
        with pytest.raises(IntegrityError), transaction.atomic():
            _contact(company, role="secondary")
        _contact(company)
        _contact(company)  # many "none" is fine
        _contact(make_company(), role="primary")  # another company is fine

    def test_archived_primary_frees_slot(self):
        company = make_company()
        first = _contact(company, role="primary")
        first.archive()
        _contact(company, role="primary")

    def test_profile_url_unique_among_active(self):
        company = make_company()
        _contact(company, profile_url="https://linkedin.example/in/a")
        with pytest.raises(IntegrityError), transaction.atomic():
            _contact(company, profile_url="https://linkedin.example/in/a")
        _contact(company, profile_url="")
        _contact(company, profile_url="")

    def test_rank_and_name_and_email_status_rules(self):
        company = make_company()
        with pytest.raises(IntegrityError), transaction.atomic():
            _contact(company, rank=0)
        with pytest.raises(IntegrityError), transaction.atomic():
            _contact(company, name="")
        with pytest.raises(IntegrityError), transaction.atomic():
            _contact(company, email="", email_status="verified")
        with pytest.raises(IntegrityError), transaction.atomic():
            _contact(company, email="a@example.com", email_status="not_found")

    def test_many_contacts_per_company(self):
        company = make_company()
        for _ in range(3):
            _contact(company)
        assert company.contacts.count() == 3

    def test_tenant_mismatch_rejected(self):
        company = make_company()
        with pytest.raises(TenantMismatchError):
            Contact(company=company, data_source=make_data_source(), name="X").save()


class TestServices:
    def test_create_contact_defaults_status_from_email(self):
        company = make_company()
        source = make_data_source(client=company.client)
        with_email = services.create_contact(company, source, " Aya ", email="a@example.com")
        without = services.create_contact(company, source, "Bo")
        assert with_email.name == "Aya"
        assert with_email.email_status == EmailStatus.UNVERIFIED
        assert without.email_status == EmailStatus.UNKNOWN

    def test_validation(self):
        company = make_company()
        source = make_data_source(client=company.client)
        with pytest.raises(ValidationError):
            services.create_contact(company, make_data_source(), "A")
        with pytest.raises(ValidationError):
            services.create_contact(company, source, "")
        with pytest.raises(ValidationError):
            services.create_contact(company, source, "A", email="not-an-email")
        with pytest.raises(ValidationError):
            services.create_contact(company, source, "A", email_status="verified")
        with pytest.raises(ValidationError):
            services.create_contact(company, source, "A", email="a@e.com", email_status="not_found")
        company.archive()
        with pytest.raises(ValidationError):
            services.create_contact(company, source, "A")

    def test_duplicate_profile_url_friendly_error(self):
        company = make_company()
        source = make_data_source(client=company.client)
        services.create_contact(company, source, "A", profile_url="https://x.example/in/a")
        with pytest.raises(ValidationError, match="already a contact"):
            services.create_contact(company, source, "B", profile_url="https://x.example/in/a")

    def test_new_primary_demotes_old_primary(self):
        company = make_company()
        source = make_data_source(client=company.client)
        first = services.create_contact(company, source, "A", role="primary")
        second = services.create_contact(company, source, "B", role="primary")
        first.refresh_from_db()
        assert first.role == ContactRole.NONE
        assert Contact.objects.primary_for(company) == second

    def test_set_contact_role(self):
        company = make_company()
        a = _contact(company, role="primary")
        b = _contact(company)
        services.set_contact_role(b, "primary")
        a.refresh_from_db()
        assert a.role == "none"
        assert Contact.objects.primary_for(company) == b
        services.set_contact_role(b, "none")
        assert Contact.objects.primary_for(company) is None
        with pytest.raises(ValidationError):
            services.set_contact_role(b, "boss")
        b.archive()
        with pytest.raises(ValidationError):
            services.set_contact_role(b, "primary")

    def test_update_contact(self):
        contact = _contact(make_company())
        services.update_contact(contact, email="n@example.com", email_status="verified", rank=2)
        contact.refresh_from_db()
        assert (contact.email, contact.email_status, contact.rank) == (
            "n@example.com",
            "verified",
            2,
        )
        with pytest.raises(ValidationError):
            services.update_contact(contact, role="primary")
        with pytest.raises(ValidationError):
            services.update_contact(contact, email_status="not_found")
        contact.archive()
        with pytest.raises(ValidationError):
            services.update_contact(contact, title="CEO")

    def test_restore_when_slot_taken_returns_as_none(self):
        company = make_company()
        old = _contact(company, role="primary")
        old.archive()
        new = _contact(company, role="primary")
        services.restore_contact(old)
        old.refresh_from_db()
        assert not old.is_archived
        assert old.role == "none"
        assert Contact.objects.primary_for(company) == new

    def test_restore_keeps_role_when_free(self):
        old = _contact(make_company(), role="secondary")
        old.archive()
        services.restore_contact(old)
        old.refresh_from_db()
        assert old.role == "secondary"

    def test_ranked_and_active(self):
        company = make_company()
        c3 = _contact(company, rank=None, name="Z")
        c2 = _contact(company, rank=2, name="B")
        c1 = _contact(company, rank=1, name="C")
        gone = _contact(company, rank=1, name="D")
        gone.archive()
        assert list(Contact.objects.for_company(company).active().ranked()) == [c1, c2, c3]


class TestErase:
    def test_erase_personal_data(self):
        contact = _contact(
            make_company(),
            role="primary",
            email="a@example.com",
            email_status="verified",
            profile_url="https://x.example/in/a",
            rank=1,
        )
        pk = contact.pk
        contact.erase_personal_data()
        contact.refresh_from_db()
        assert contact.pk == pk
        assert contact.name == "Erased contact"
        assert (contact.title, contact.email, contact.profile_url) == ("", "", "")
        assert contact.relevance_reason == ""
        assert contact.email_status == EmailStatus.UNKNOWN
        assert contact.role == ContactRole.NONE
        assert contact.rank is None
        assert contact.is_archived
        assert contact.erased_at is not None
        stamp = contact.erased_at
        contact.erase_personal_data()
        assert contact.erased_at == stamp

    def test_erased_cannot_be_restored(self):
        contact = _contact(make_company())
        contact.erase_personal_data()
        with pytest.raises(ValidationError):
            services.restore_contact(contact)


class TestAdmin:
    def test_no_add_or_delete_and_actions(self):
        admin: Any = site._registry[Contact]
        request: Any = None
        assert not admin.has_add_permission(request)
        assert not admin.has_delete_permission(request)
        contact = _contact(make_company(), role="primary")
        admin.archive_selected(request, Contact.objects.filter(pk=contact.pk))
        contact.refresh_from_db()
        assert contact.is_archived
        admin.restore_selected(request, Contact.objects.filter(pk=contact.pk))
        contact.refresh_from_db()
        assert not contact.is_archived
