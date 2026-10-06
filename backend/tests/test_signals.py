"""Signal model, freshness, superseding and the computed trigger (issue #41)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from django.contrib.admin.sites import site
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.core.base import ImmutableRecordError, TenantMismatchError
from apps.research import services
from apps.research.models import Signal, SignalType
from tests.factories import make_company, make_data_source, make_signal, make_user

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 6, 1, 12, tzinfo=UTC)


def _signal(company, **kw):
    return make_signal(company=company, **kw)


class TestConstraints:
    def test_thirteen_types(self):
        assert len(SignalType.values) == 13
        assert "funding" in SignalType.values

    def test_source_required_in_database(self):
        company = make_company()
        sig = Signal(
            company=company,
            client_id=company.client_id,
            type="funding",
            evidence="x",
            event_date=date(2026, 1, 1),
        )
        # bulk_create skips save(), so this reaches the database NOT NULL on data_source_id.
        with pytest.raises(IntegrityError), transaction.atomic():
            Signal.objects.bulk_create([sig])

    def test_event_date_required_in_database(self):
        company = make_company()
        source = make_data_source(client=company.client)
        sig = Signal(company=company, data_source=source, type="funding", evidence="x")
        with pytest.raises(IntegrityError), transaction.atomic():
            sig.save()

    def test_invalid_type_rejected_by_database(self):
        company = make_company()
        with pytest.raises(IntegrityError), transaction.atomic():
            _signal(company, type="bogus")

    def test_empty_evidence_rejected_by_database(self):
        company = make_company()
        with pytest.raises(IntegrityError), transaction.atomic():
            _signal(company, evidence="")

    def test_many_signals_per_company(self):
        company = make_company()
        _signal(company)
        _signal(company, type="new_office")
        assert company.signals.count() == 2

    def test_client_copied_and_source_client_checked(self):
        company = make_company()
        sig = _signal(company)
        assert sig.client_id == company.client_id
        other = make_data_source()
        with pytest.raises(TenantMismatchError):
            Signal(
                company=company,
                data_source=other,
                type="funding",
                evidence="x",
                event_date=date(2026, 1, 1),
            ).save()

    def test_supersedes_other_company_rejected(self):
        a, b = make_company(), make_company()
        sig_a = _signal(a)
        with pytest.raises(TenantMismatchError):
            Signal(
                company=b,
                data_source=make_data_source(client=b.client),
                type="funding",
                evidence="x",
                event_date=date(2026, 1, 1),
                supersedes=sig_a,
            ).save()


class TestAppendOnly:
    def test_cannot_update_or_delete(self):
        sig = _signal(make_company())
        sig.evidence = "changed"
        with pytest.raises(ImmutableRecordError):
            sig.save()
        with pytest.raises(ImmutableRecordError):
            sig.delete()
        with pytest.raises(ImmutableRecordError):
            Signal.objects.all().update(evidence="x")
        with pytest.raises(ImmutableRecordError):
            Signal.objects.all().delete()

    def test_admin_read_only(self):
        admin: Any = site._registry[Signal]
        request: Any = None
        assert not admin.has_add_permission(request)
        assert not admin.has_change_permission(request)
        assert not admin.has_delete_permission(request)


class TestFreshness:
    def test_null_expiry_counts_as_fresh(self):
        company = make_company()
        sig = _signal(company, expires_at=None)
        assert sig in Signal.objects.fresh(NOW)
        assert sig.is_fresh(NOW)

    def test_boundary_expiry_instant_is_stale(self):
        company = make_company()
        sig = _signal(company, expires_at=NOW)
        assert sig not in Signal.objects.fresh(NOW)
        assert sig in Signal.objects.fresh(NOW - timedelta(microseconds=1))
        assert sig not in Signal.objects.fresh(NOW + timedelta(seconds=1))

    def test_default_as_of_is_now(self):
        company = make_company()
        past = _signal(company, expires_at=datetime.now(UTC) - timedelta(days=1))
        future = _signal(company, expires_at=datetime.now(UTC) + timedelta(days=1))
        fresh = set(Signal.objects.fresh())
        assert future in fresh
        assert past not in fresh

    def test_naive_as_of_rejected(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            Signal.objects.fresh(datetime(2026, 1, 1))

    def test_current_excludes_superseded(self):
        company = make_company()
        old = _signal(company)
        new = services.supersede_signal(old, old.data_source, "Corrected amount", date(2026, 3, 2))
        assert list(Signal.objects.current()) == [new]
        assert not old.is_current
        assert new.is_current
        assert old not in Signal.objects.fresh()


class TestServices:
    def test_create_signal(self):
        company = make_company()
        source = make_data_source(client=company.client)
        user = make_user()
        sig = services.create_signal(
            company, source, "funding", "  Series B  ", date(2026, 2, 1), user=user
        )
        assert sig.evidence == "Series B"
        assert sig.created_by == user
        assert sig.expires_at is None

    def test_source_and_date_required(self):
        company = make_company()
        source = make_data_source(client=company.client)
        with pytest.raises(ValidationError):
            services.create_signal(company, None, "funding", "x", date(2026, 1, 1))  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            services.create_signal(company, source, "funding", "x", None)

    def test_blank_evidence_and_bad_type_rejected(self):
        company = make_company()
        source = make_data_source(client=company.client)
        with pytest.raises(ValidationError):
            services.create_signal(company, source, "funding", "   ", date(2026, 1, 1))
        with pytest.raises(ValidationError):
            services.create_signal(company, source, "nope", "x", date(2026, 1, 1))

    def test_other_clients_source_rejected(self):
        company = make_company()
        with pytest.raises(ValidationError):
            services.create_signal(company, make_data_source(), "funding", "x", date(2026, 1, 1))

    def test_archived_company_rejected(self):
        company = make_company()
        source = make_data_source(client=company.client)
        company.archive()
        with pytest.raises(ValidationError):
            services.create_signal(company, source, "funding", "x", date(2026, 1, 1))

    def test_supersede_rules(self):
        company = make_company()
        old = _signal(company)
        services.supersede_signal(old, old.data_source, "fix", date(2026, 3, 3), type="new_office")
        with pytest.raises(ValidationError, match="already superseded"):
            services.supersede_signal(old, old.data_source, "again", date(2026, 3, 4))
        other = _signal(make_company(campaign=company.campaign))
        with pytest.raises(ValidationError, match="different company"):
            services.create_signal(
                company, other.data_source, "funding", "x", date(2026, 1, 1), supersedes=other
            )

    def test_retire_signal_drops_trigger(self):
        company = make_company()
        sig = _signal(company)
        assert services.company_trigger_state(company).triggered
        retired = services.retire_signal(sig, sig.data_source, "Not a real funding round")
        assert retired.type == SignalType.OTHER
        assert retired.supersedes == sig
        state = services.company_trigger_state(company)
        assert not state.triggered
        assert state.signals == []


class TestTriggerState:
    def test_zero_signals_is_no(self):
        company = make_company()
        state = services.company_trigger_state(company, NOW)
        assert state.triggered is False
        assert state.label == "No"
        assert state.signals == []
        assert state.as_of == NOW

    def test_yes_with_fresh_signal_and_no_stored_flag(self):
        company = make_company()
        sig = _signal(company, expires_at=NOW + timedelta(days=1))
        state = services.company_trigger_state(company, NOW)
        assert state.label == "Yes"
        assert state.signals == [sig]
        assert not hasattr(sig, "active")
        assert not hasattr(company, "trigger")

    def test_goes_stale_over_time(self):
        company = make_company()
        _signal(company, expires_at=NOW + timedelta(days=1))
        assert services.company_trigger_state(company, NOW).triggered
        assert not services.company_trigger_state(company, NOW + timedelta(days=1)).triggered

    def test_scoped_to_company_and_ordered_newest_event_first(self):
        company = make_company()
        older = _signal(company, event_date=date(2026, 1, 1))
        newer = _signal(company, event_date=date(2026, 5, 1))
        _signal(make_company())
        assert services.company_trigger_state(company).signals == [newer, older]

    def test_default_as_of_is_now(self):
        company = make_company()
        _signal(company)
        state = services.company_trigger_state(company)
        assert state.triggered
