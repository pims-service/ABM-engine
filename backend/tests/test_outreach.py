"""Outreach angle, message and activity models and services (issue #43)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.admin.sites import site
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.core.base import ImmutableRecordError, TenantMismatchError
from apps.outreach import decisions, services
from apps.outreach.models import (
    Activity,
    AngleSignal,
    AngleSource,
    Message,
    MessageSignal,
    MessageSource,
    MessageStatus,
    OutreachAngle,
)
from apps.outreach.services import FeedbackKind, HumanDecisionRequired
from tests.factories import (
    allow_outreach,
    make_angle,
    make_campaign,
    make_company,
    make_contact,
    make_data_source,
    make_message,
    make_research,
    make_signal,
    make_user,
)

pytestmark = pytest.mark.django_db


def _hold(company: Any) -> str:
    return "hold"


def _none(company: Any) -> None:
    return None


@pytest.fixture
def company():
    return make_company()


@pytest.fixture
def angle(company):
    return make_angle(company=company)


@pytest.fixture
def contact(company):
    return make_contact(company=company)


# ------------------------------------------------------------------ angles


class TestAngle:
    def test_create_with_evidence_copies_client(self, company):
        signal = make_signal(company=company)
        source = make_data_source(client=company.client)
        angle = services.create_angle(
            company, " Support BD ", " because hiring ", signals=[signal, signal],
            data_sources=[source], model_name="m", prompt_version="p1", schema_version="s1",
        )  # fmt: skip
        assert angle.client_id == company.client_id
        assert angle.angle == "Support BD"
        assert angle.rationale == "because hiring"
        assert list(angle.signals.all()) == [signal]
        assert list(angle.data_sources.all()) == [source]
        assert AngleSignal.objects.get().client_id == company.client_id
        assert AngleSource.objects.get().client_id == company.client_id
        assert (angle.model_name, angle.prompt_version, angle.schema_version) == ("m", "p1", "s1")

    def test_angle_without_evidence_is_allowed(self, company):
        angle = services.create_angle(company, "Support BD", "campaign rules")
        assert angle.signals.count() == 0

    def test_one_angle_many_messages_without_duplication(self, company, angle):
        email = make_message(angle=angle, contact=make_contact(company=company))
        call = make_message(angle=angle, contact=email.contact, channel="call", subject="")
        arabic = make_message(
            angle=angle, contact=email.contact, channel="linkedin", language="ar", subject=""
        )
        assert OutreachAngle.objects.count() == 1
        assert set(angle.messages.all()) == {email, call, arabic}
        assert {m.angle_id for m in angle.messages.all()} == {angle.pk}

    def test_signal_of_other_company_rejected(self, company):
        other = make_signal(company=make_company(campaign=company.campaign))
        with pytest.raises(ValidationError, match="different company"):
            services.create_angle(company, "a", "r", signals=[other])
        assert OutreachAngle.objects.count() == 0

    def test_source_of_other_client_rejected(self, company):
        with pytest.raises(ValidationError, match="different client"):
            services.create_angle(company, "a", "r", data_sources=[make_data_source()])

    def test_text_required(self, company):
        with pytest.raises(ValidationError):
            services.create_angle(company, "  ", "r")
        with pytest.raises(ValidationError):
            services.create_angle(company, "a", "")

    def test_archived_company_rejected(self, company):
        company.archive()
        with pytest.raises(ValidationError, match="read-only"):
            services.create_angle(company, "a", "r")

    def test_database_rejects_empty_text(self, company):
        with pytest.raises(IntegrityError), transaction.atomic():
            OutreachAngle(company=company, angle="", rationale="r").save()

    def test_append_only(self, angle):
        angle.angle = "changed"
        with pytest.raises(ImmutableRecordError):
            angle.save()
        with pytest.raises(ImmutableRecordError):
            angle.delete()
        with pytest.raises(ImmutableRecordError):
            OutreachAngle.objects.update(angle="x")
        with pytest.raises(ImmutableRecordError):
            OutreachAngle.objects.all().delete()

    def test_latest_for(self, company):
        assert OutreachAngle.objects.latest_for(company) is None
        first = make_angle(company=company)
        second = make_angle(company=company)
        assert OutreachAngle.objects.latest_for(company) == second
        assert OutreachAngle.objects.latest_for(company.pk) == second
        assert list(OutreachAngle.objects.for_company(company).latest_first()) == [second, first]
        assert OutreachAngle.objects.latest_for(make_company()) is None

    def test_evidence_link_checks_tenant(self, company, angle):
        foreign_signal = make_signal()
        with pytest.raises(TenantMismatchError):
            AngleSignal(angle=angle, signal=foreign_signal).save()
        with pytest.raises(TenantMismatchError):
            AngleSource(angle=angle, data_source=make_data_source()).save()

    def test_evidence_links_are_unique_and_append_only(self, company, angle):
        signal = make_signal(company=company)
        link = AngleSignal(angle=angle, signal=signal)
        link.save()
        with pytest.raises(IntegrityError), transaction.atomic():
            AngleSignal(angle=angle, signal=signal).save()
        with pytest.raises(ImmutableRecordError):
            link.delete()

    def test_scoped_by_client(self, company, angle):
        make_angle()
        assert list(OutreachAngle.objects.for_client(company.client)) == [angle]


# ------------------------------------------------------------------ messages


class TestMessageCreate:
    def test_draft_message_carries_channel_language_and_angle(self, company, angle, contact):
        message = services.create_message(
            angle, contact, "email", " EN ", " Hello ", subject=" Hi ",
            decision_lookup=allow_outreach, model_name="m",
        )  # fmt: skip
        assert message.status == MessageStatus.DRAFT
        assert (message.channel, message.language) == ("email", "en")
        assert (message.subject, message.body) == ("Hi", "Hello")
        assert message.company_id == company.pk
        assert message.client_id == company.client_id
        assert message.angle == angle
        assert message.contact == contact
        assert message.approved_by is None
        assert message.is_followup is False

    def test_language_must_be_campaign_language(self, angle, contact):
        with pytest.raises(ValidationError, match="not an outreach language"):
            services.create_message(
                angle, contact, "call", "fr", "x", decision_lookup=allow_outreach
            )

    def test_language_checked_against_current_campaign_profile(self):
        campaign = make_campaign(profile__outreach_languages=["ar"])
        company = make_company(campaign=campaign)
        angle = make_angle(company=company)
        contact = make_contact(company=company)
        with pytest.raises(ValidationError):
            services.create_message(
                angle, contact, "call", "en", "x", decision_lookup=allow_outreach
            )
        message = services.create_message(
            angle, contact, "call", "ar", "x", decision_lookup=allow_outreach
        )
        assert message.language == "ar"

    def test_unknown_channel(self, angle, contact):
        with pytest.raises(ValidationError, match="Unknown channel"):
            services.create_message(
                angle, contact, "fax", "en", "x", decision_lookup=allow_outreach
            )

    def test_subject_rules(self, angle, contact):
        with pytest.raises(ValidationError, match="needs a subject"):
            services.create_message(
                angle, contact, "email", "en", "x", decision_lookup=allow_outreach
            )
        with pytest.raises(ValidationError, match="Only emails"):
            services.create_message(
                angle, contact, "call", "en", "x", subject="s", decision_lookup=allow_outreach
            )

    def test_body_required(self, angle, contact):
        with pytest.raises(ValidationError):
            services.create_message(
                angle, contact, "call", "en", "  ", decision_lookup=allow_outreach
            )

    def test_contact_must_belong_to_company_and_be_active(self, company, angle):
        with pytest.raises(ValidationError, match="different company"):
            services.create_message(
                angle, make_contact(), "call", "en", "x", decision_lookup=allow_outreach
            )
        archived = make_contact(company=company)
        archived.archive()
        with pytest.raises(ValidationError, match="archived"):
            services.create_message(
                angle, archived, "call", "en", "x", decision_lookup=allow_outreach
            )

    def test_archived_company(self, company, angle, contact):
        company.archive()
        with pytest.raises(ValidationError, match="read-only"):
            services.create_message(
                angle, contact, "call", "en", "x", decision_lookup=allow_outreach
            )

    def test_followup_flag(self, angle, contact):
        message = services.create_message(
            angle, contact, "call", "en", "x", is_followup=True, decision_lookup=allow_outreach
        )
        assert message.is_followup is True


class TestHumanDecisionRule:
    @pytest.mark.parametrize("lookup", [_hold, _none, lambda c: "skip"])
    def test_create_requires_add(self, angle, contact, lookup):
        with pytest.raises(HumanDecisionRequired):
            services.create_message(angle, contact, "call", "en", "x", decision_lookup=lookup)
        assert Message.objects.count() == 0

    def test_approve_requires_add(self, angle, contact):
        message = make_message(angle=angle, contact=contact)
        with pytest.raises(HumanDecisionRequired, match="latest decision: hold"):
            services.approve_message(message, make_user(), decision_lookup=_hold)
        message.refresh_from_db()
        assert message.status == MessageStatus.DRAFT

    def test_decision_flipped_after_draft_blocks_approval(self, angle, contact):
        message = make_message(angle=angle, contact=contact)
        with pytest.raises(HumanDecisionRequired):
            services.approve_message(message, make_user(), decision_lookup=_none)

    def test_default_lookup_is_the_adapter(self, angle, contact, monkeypatch):
        monkeypatch.setattr(services, "latest_human_decision", lambda company: "add")
        message = services.create_message(angle, contact, "call", "en", "x")
        services.approve_message(message, make_user())
        monkeypatch.setattr(services, "latest_human_decision", lambda company: "skip")
        with pytest.raises(HumanDecisionRequired):
            services.create_message(angle, contact, "call", "en", "y")

    def test_supersede_also_needs_add(self, angle, contact):
        old = make_message(angle=angle, contact=contact)
        with pytest.raises(HumanDecisionRequired):
            services.supersede_message(old, "new", decision_lookup=_hold)


class TestDecisionAdapter:
    def test_model_missing_returns_none(self, company, monkeypatch):
        monkeypatch.setattr(decisions, "_human_decision_model", lambda: None)
        assert decisions.latest_human_decision(company) is None

    def test_reads_latest_for_when_available(self, company, monkeypatch):
        seen = []

        class Manager:
            def latest_for(self, c):
                seen.append(c)
                return SimpleNamespace(decision="add")

        monkeypatch.setattr(
            decisions, "_human_decision_model", lambda: SimpleNamespace(objects=Manager())
        )
        assert decisions.latest_human_decision(company) == "add"
        assert seen == [company]

    def test_falls_back_to_latest_decided_at(self, company, monkeypatch):
        calls = []

        class Query:
            def __init__(self, rows):
                self.rows = rows

            def order_by(self, *fields):
                calls.append(fields)
                return self

            def first(self):
                return self.rows[0] if self.rows else None

        class Manager:
            rows: list[Any] = []  # noqa: RUF012

            def filter(self, **kwargs):
                assert kwargs == {"company": company}
                return Query(self.rows)

        manager = Manager()
        monkeypatch.setattr(
            decisions, "_human_decision_model", lambda: SimpleNamespace(objects=manager)
        )
        assert decisions.latest_human_decision(company) is None
        manager.rows = [SimpleNamespace(decision="hold")]
        assert decisions.latest_human_decision(company) == "hold"
        assert calls[-1] == ("-decided_at", "-created_at", "-id")

    def test_real_adapter_on_this_branch(self, company):
        """Without #42 the model is missing and the answer is None (fail closed). Once #42 is
        merged, no decision row means None too, so this holds either way."""
        assert decisions.latest_human_decision(company) is None


class TestApproveAndExport:
    def test_approve_then_export(self, company, angle, contact):
        message = make_message(angle=angle, contact=contact, channel="call", subject="")
        user = make_user()
        services.approve_message(message, user, decision_lookup=allow_outreach)
        message.refresh_from_db()
        assert message.status == MessageStatus.APPROVED
        assert message.approved_by == user
        assert message.approved_at is not None

        exporter = make_user()
        services.mark_exported(message, exporter)
        message.refresh_from_db()
        assert message.status == MessageStatus.EXPORTED
        assert message.exported_at is not None
        activity = Activity.objects.get(type="exported")
        assert activity.actor == exporter
        assert activity.company == company
        assert activity.payload == {"message_id": str(message.pk), "channel": "call"}

    def test_cannot_approve_twice_or_skip_approval(self, angle, contact):
        message = make_message(angle=angle, contact=contact)
        with pytest.raises(ValidationError, match="Only an approved"):
            services.mark_exported(message)
        services.approve_message(message, make_user(), decision_lookup=allow_outreach)
        with pytest.raises(ValidationError, match="Only a draft"):
            services.approve_message(message, make_user(), decision_lookup=allow_outreach)

    def test_superseded_message_cannot_be_approved(self, angle, contact):
        old = make_message(angle=angle, contact=contact)
        services.supersede_message(old, "newer", decision_lookup=allow_outreach)
        with pytest.raises(ValidationError, match="superseded"):
            services.approve_message(old, make_user(), decision_lookup=allow_outreach)

    def test_archived_contact_blocks_approval(self, angle, contact):
        message = make_message(angle=angle, contact=contact)
        contact.archive()
        with pytest.raises(ValidationError, match="archived"):
            services.approve_message(message, make_user(), decision_lookup=allow_outreach)

    def test_archived_company_blocks_approval(self, company, angle, contact):
        message = make_message(angle=angle, contact=contact)
        company.archive()
        with pytest.raises(ValidationError, match="read-only"):
            services.approve_message(message, make_user(), decision_lookup=allow_outreach)

    def test_stale_instance_keeps_working_after_service_call(self, angle, contact):
        message = make_message(angle=angle, contact=contact)
        services.approve_message(message, make_user(), decision_lookup=allow_outreach)
        services.mark_exported(message)
        assert message.status == MessageStatus.EXPORTED  # refreshed in place


class TestImmutability:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("body", "other text"),
            ("subject", "other subject"),
            ("language", "ar"),
            ("channel", "linkedin"),
            ("is_followup", True),
            ("model_name", "x"),
        ],
    )
    def test_text_and_identity_cannot_change(self, angle, contact, field, value):
        message = make_message(angle=angle, contact=contact)
        message = Message.objects.get(pk=message.pk)
        setattr(message, field, value)
        with pytest.raises(ImmutableRecordError, match="immutable"):
            message.save()
        assert getattr(Message.objects.get(pk=message.pk), field) != value

    def test_recipient_and_angle_cannot_change(self, company, angle, contact):
        message = Message.objects.get(pk=make_message(angle=angle, contact=contact).pk)
        message.contact = make_contact(company=company)
        with pytest.raises(ImmutableRecordError):
            message.save()
        message = Message.objects.get(pk=message.pk)
        message.angle = make_angle(company=company)
        with pytest.raises(ImmutableRecordError):
            message.save()

    def test_status_only_moves_forward_one_step(self, angle, contact):
        message = Message.objects.get(pk=make_message(angle=angle, contact=contact).pk)
        message.status = MessageStatus.EXPORTED
        with pytest.raises(ImmutableRecordError, match="forward"):
            message.save()
        services.approve_message(message, make_user(), decision_lookup=allow_outreach)
        message.status = MessageStatus.DRAFT
        with pytest.raises(ImmutableRecordError, match="forward"):
            message.save()

    def test_approval_cannot_be_rewritten(self, angle, contact):
        message = make_message(angle=angle, contact=contact)
        services.approve_message(message, make_user(), decision_lookup=allow_outreach)
        message.approved_by = make_user()
        with pytest.raises(ImmutableRecordError, match="approval"):
            message.save()

    def test_never_deleted_or_bulk_updated(self, angle, contact):
        message = make_message(angle=angle, contact=contact)
        with pytest.raises(ImmutableRecordError):
            message.delete()
        with pytest.raises(ImmutableRecordError):
            Message.objects.update(body="x")
        with pytest.raises(ImmutableRecordError):
            Message.objects.all().delete()

    def test_database_constraints(self, company, angle, contact):
        base: dict[str, Any] = {
            "company": company, "contact": contact, "angle": angle, "client": company.client,
            "channel": "call", "language": "en", "body": "b",
        }  # fmt: skip
        bad: list[dict[str, Any]] = [
            {"status": "approved", "approved_at": datetime.now(UTC)},  # no approver
            {"status": "approved", "approved_by": make_user()},  # no time
            {"status": "exported", "approved_by": make_user(), "approved_at": datetime.now(UTC)},
            {"status": "bogus"},
            {"channel": "fax"},
            {"channel": "call", "subject": "only email has one"},
            {"body": ""},
            {"language": ""},
        ]
        for extra in bad:
            with pytest.raises(IntegrityError), transaction.atomic():
                Message.objects.bulk_create([Message(**{**base, **extra})])


class TestSupersede:
    def test_edit_is_a_new_row_old_untouched(self, angle, contact):
        old = make_message(angle=angle, contact=contact, body="v1")
        services.approve_message(old, make_user(), decision_lookup=allow_outreach)
        new = services.supersede_message(
            old, "v2", subject="Fresh", user=make_user(), decision_lookup=allow_outreach
        )
        old.refresh_from_db()
        assert (old.body, old.status) == ("v1", MessageStatus.APPROVED)
        assert new.supersedes == old
        assert new.status == MessageStatus.DRAFT
        assert (new.angle_id, new.contact_id, new.channel) == (
            old.angle_id,
            old.contact_id,
            "email",
        )
        assert new.language == old.language
        assert new.subject == "Fresh"
        assert old.is_current is False
        assert new.is_current is True

    def test_defaults_carry_over_evidence_and_subject(self, company, angle, contact):
        signal = make_signal(company=company)
        old = make_message(angle=angle, contact=contact, signals=[signal])
        new = services.supersede_message(old, "v2", decision_lookup=allow_outreach)
        assert new.subject == old.subject
        assert list(new.signals.all()) == [signal]
        replaced = services.supersede_message(new, "v3", signals=[], decision_lookup=allow_outreach)
        assert replaced.signals.count() == 0

    def test_language_can_change_in_a_new_row(self, angle, contact):
        old = make_message(angle=angle, contact=contact)
        new = services.supersede_message(old, "v2", language="ar", decision_lookup=allow_outreach)
        assert new.language == "ar"
        assert old.language == "en"

    def test_cannot_supersede_twice(self, angle, contact):
        old = make_message(angle=angle, contact=contact)
        services.supersede_message(old, "v2", decision_lookup=allow_outreach)
        with pytest.raises(ValidationError, match="already superseded"):
            services.supersede_message(old, "v3", decision_lookup=allow_outreach)

    def test_database_blocks_double_supersede(self, company, angle, contact):
        old = make_message(angle=angle, contact=contact)
        services.supersede_message(old, "v2", decision_lookup=allow_outreach)
        with pytest.raises(IntegrityError), transaction.atomic():
            Message(
                company=company, contact=contact, angle=angle, channel="call", language="en",
                body="b", supersedes=old,
            ).save()  # fmt: skip

    def test_supersede_from_other_company_rejected(self, company, angle, contact):
        other_message = make_message()
        with pytest.raises(ValidationError, match="different company"):
            services.create_message(
                angle, contact, "call", "en", "x", supersedes=other_message,
                decision_lookup=allow_outreach,
            )  # fmt: skip

    def test_latest_for_and_current(self, company, angle, contact):
        assert Message.objects.latest_for(angle, contact, "email") is None
        v1 = make_message(angle=angle, contact=contact)
        v2 = services.supersede_message(v1, "v2", decision_lookup=allow_outreach)
        followup = make_message(angle=angle, contact=contact, is_followup=True)
        assert Message.objects.latest_for(angle, contact, "email") == v2
        assert Message.objects.latest_for(angle, contact, "email", is_followup=True) == followup
        assert Message.objects.latest_for(angle, contact, "call") is None
        assert set(Message.objects.for_company(company).current()) == {v2, followup}


class TestGrounding:
    def test_message_stores_evidence_references(self, company, angle, contact):
        signal = make_signal(company=company)
        source = signal.data_source
        message = make_message(
            angle=angle, contact=contact, signals=[signal], data_sources=[source]
        )
        assert list(message.signals.all()) == [signal]
        assert list(message.data_sources.all()) == [source]
        assert MessageSignal.objects.get().client_id == company.client_id
        assert MessageSource.objects.get().client_id == company.client_id

    def test_signal_of_other_company_rejected(self, angle, contact):
        with pytest.raises(ValidationError, match="different company"):
            make_message(angle=angle, contact=contact, signals=[make_signal()])
        assert Message.objects.count() == 0

    def test_source_of_other_client_rejected(self, angle, contact):
        with pytest.raises(ValidationError, match="different client"):
            make_message(angle=angle, contact=contact, data_sources=[make_data_source()])

    def test_invented_source_rejected(self, company, angle, contact):
        orphan = make_data_source(client=company.client)  # same client, tied to nothing
        with pytest.raises(ValidationError, match="not part of this company"):
            make_message(angle=angle, contact=contact, data_sources=[orphan])

    def test_reachable_through_angle_research_or_contact(self, company, contact):
        angle_source = make_data_source(client=company.client)
        angle = make_angle(company=company, data_sources=[angle_source])
        research = make_research(company=company)
        message = make_message(
            angle=angle,
            contact=contact,
            data_sources=[angle_source, research.data_source, contact.data_source],
        )
        assert message.data_sources.count() == 3

    def test_reachable_through_angle_signal(self, company, contact):
        signal = make_signal(company=company)
        angle = make_angle(company=company, signals=[signal])
        message = make_message(angle=angle, contact=contact, data_sources=[signal.data_source])
        assert message.data_sources.count() == 1

    def test_failed_message_leaves_no_rows(self, angle, contact):
        with pytest.raises(ValidationError):
            make_message(angle=angle, contact=contact, signals=[make_signal()])
        assert MessageSignal.objects.count() == 0

    def test_tenant_checks_on_direct_writes(self, company, angle, contact):
        message = make_message(angle=angle, contact=contact)
        with pytest.raises(TenantMismatchError):
            MessageSignal(message=message, signal=make_signal()).save()
        with pytest.raises(TenantMismatchError):
            MessageSource(message=message, data_source=make_data_source()).save()
        other_company = make_company(campaign=company.campaign)
        base: dict[str, Any] = {"company": other_company, "channel": "call", "language": "en"}
        with pytest.raises(TenantMismatchError, match="angle"):
            Message(
                contact=make_contact(company=other_company), angle=angle, body="b", **base
            ).save()
        with pytest.raises(TenantMismatchError, match="contact"):
            Message(
                contact=contact, angle=make_angle(company=other_company), body="b", **base
            ).save()
        with pytest.raises(TenantMismatchError, match="supersedes"):
            Message(
                contact=make_contact(company=other_company),
                angle=make_angle(company=other_company),
                supersedes=message, body="b", **base,
            ).save()  # fmt: skip

    def test_client_mismatch_rejected(self, company, angle, contact):
        other = make_company()
        with pytest.raises(TenantMismatchError):
            Message(
                company=company, client=other.client, contact=contact, angle=angle,
                channel="call", language="en", body="b",
            ).save()  # fmt: skip


# ------------------------------------------------------------------ activities


class TestActivity:
    def test_one_call_inserts_a_row_with_tenancy(self, company):
        user = make_user()
        activity = services.record_activity(company, "contacted", user, channel="email", n=2)
        assert activity.client_id == company.client_id
        assert activity.campaign_id == company.campaign_id
        assert activity.actor == user
        assert activity.payload == {"channel": "email", "n": 2}
        assert services.log_activity is services.record_activity

    def test_actor_optional_and_payload_defaults_empty(self, company):
        assert services.record_activity(company, "researched").actor is None
        assert Activity.objects.get().payload == {}

    def test_every_type_is_accepted(self, company):
        for kind in (
            "researched", "recommended", "decided", "contacted", "replied", "meeting_booked",
            "exported",
        ):  # fmt: skip
            services.record_activity(company, kind)
        assert Activity.objects.count() == 7

    def test_unknown_type_and_bad_payload(self, company):
        with pytest.raises(ValidationError, match="Unknown activity type"):
            services.record_activity(company, "bogus")
        with pytest.raises(ValidationError, match="JSON"):
            services.record_activity(company, "contacted", when=object())
        with pytest.raises(IntegrityError), transaction.atomic():
            Activity(company=company, type="bogus").save()

    def test_timeline_newest_first_and_filtered(self, company):
        now = datetime.now(UTC)
        old = services.record_activity(company, "researched", created_at=now - timedelta(days=2))
        mid = services.record_activity(company, "contacted", created_at=now - timedelta(days=1))
        new = services.record_activity(company, "replied", created_at=now)
        services.record_activity(make_company(), "replied")
        assert list(Activity.objects.timeline(company)) == [new, mid, old]
        assert list(Activity.objects.timeline(company, ["contacted", "researched"])) == [mid, old]

    def test_for_campaign_and_feedback_manager(self, company):
        services.record_activity(company, "contacted")
        sibling = make_company(campaign=company.campaign)
        services.record_activity(sibling, "replied")
        services.record_feedback(company, "wrong_buyer")
        services.record_activity(make_company(), "contacted")
        assert Activity.objects.for_campaign(company.campaign).count() == 3
        assert Activity.objects.for_campaign(company.campaign, "replied").count() == 1
        assert Activity.objects.for_campaign(company.campaign).feedback().count() == 1

    def test_isolated_between_clients(self, company):
        services.record_activity(company, "contacted")
        services.record_activity(make_company(), "contacted")
        assert Activity.objects.for_client(company.client).count() == 1

    def test_append_only(self, company):
        activity = services.record_activity(company, "contacted")
        activity.payload = {"x": 1}
        with pytest.raises(ImmutableRecordError):
            activity.save()
        with pytest.raises(ImmutableRecordError):
            activity.delete()
        with pytest.raises(ImmutableRecordError):
            Activity.objects.update(type="replied")

    def test_campaign_must_match_company(self, company):
        other_campaign = make_campaign(client=company.client)
        with pytest.raises(TenantMismatchError, match="campaign"):
            Activity(company=company, campaign=other_campaign, type="contacted").save()

    def test_str(self, company):
        assert str(services.record_activity(company, "contacted")).startswith("contacted @")


class TestFeedback:
    @pytest.mark.parametrize("kind", [k for k in FeedbackKind.values if k != "other"])
    def test_every_flag_is_valid(self, company, kind):
        activity = services.record_feedback(company, kind, actor=make_user())
        assert activity.type == "feedback"
        assert activity.payload == {"kind": kind}

    def test_brief_flags_are_the_nine_expected(self):
        assert set(FeedbackKind.values) == {
            "wrong_buyer", "not_b2b", "unsuitable_industry", "not_a_trigger",
            "ceo_should_be_primary", "ceo_should_not_be_primary", "government_company",
            "incorrect_company_data", "other",
        }  # fmt: skip

    def test_target_and_note_stored(self, company):
        signal = make_signal(company=company)
        activity = services.record_feedback(
            company, "not_a_trigger", target={"type": "signal", "id": str(signal.pk)}, note=" meh "
        )
        assert activity.payload == {
            "kind": "not_a_trigger",
            "target": {"type": "signal", "id": str(signal.pk)},
            "note": "meh",
        }

    def test_record_activity_validates_feedback_directly(self, company):
        with pytest.raises(ValidationError):
            services.record_activity(company, "feedback", kind="nope")

    def test_other_needs_note(self, company):
        with pytest.raises(ValidationError, match="needs a note"):
            services.record_feedback(company, "other")
        assert services.record_feedback(company, "other", note="odd case").payload["note"]

    def test_invalid_kind_and_extra_fields(self, company):
        with pytest.raises(ValidationError, match="must be one of"):
            services.record_feedback(company, "bad")
        with pytest.raises(ValidationError, match="Unknown feedback fields"):
            services.record_activity(company, "feedback", kind="wrong_buyer", colour="red")
        with pytest.raises(ValidationError, match="must be one of"):
            services.record_activity(company, "feedback")

    def test_note_validation(self, company):
        with pytest.raises(ValidationError, match="at most"):
            services.record_feedback(company, "wrong_buyer", note="x" * 2001)
        with pytest.raises(ValidationError, match="at most"):
            services.record_activity(company, "feedback", kind="wrong_buyer", note=5)

    def test_targets_for_every_type(self, company, angle, contact):
        message = make_message(angle=angle, contact=contact)
        for kind, obj in (
            ("company", company), ("contact", contact), ("angle", angle), ("message", message),
        ):  # fmt: skip
            activity = services.record_feedback(
                company, "wrong_buyer", target={"type": kind, "id": str(obj.pk)}
            )
            assert activity.payload["target"] == {"type": kind, "id": str(obj.pk)}

    def test_bad_targets(self, company):
        foreign_signal = make_signal()
        bad: list[Any] = [
            "signal",
            {"type": "signal"},
            {"type": "widget", "id": str(uuid.uuid4())},
            {"type": "signal", "id": "not-a-uuid"},
            {"type": "signal", "id": str(uuid.uuid4())},
            {"type": "signal", "id": str(foreign_signal.pk)},
            {"type": "company", "id": str(uuid.uuid4())},
        ]
        for target in bad:
            with pytest.raises(ValidationError):
                services.record_feedback(company, "not_a_trigger", target=target)
        assert Activity.objects.count() == 0


# ------------------------------------------------------------------ admin


@pytest.mark.parametrize("model", [OutreachAngle, Message, Activity])
def test_admin_is_read_only(model):
    admin = site._registry[model]
    request: Any = SimpleNamespace(user=make_user(is_superuser=True, is_staff=True))
    assert not admin.has_add_permission(request)
    assert not admin.has_change_permission(request)
    assert not admin.has_delete_permission(request)
