"""ICPAssessment, AIRecommendation, HumanDecision: history, latest, separation (issue #42)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.contrib.admin.sites import site
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import ProtectedError

from apps.core.base import ImmutableRecordError, TenantMismatchError
from apps.research import services
from apps.research.models import (
    AIRecommendation,
    HumanDecision,
    ICPAssessment,
)
from tests.factories import (
    make_ai_recommendation,
    make_campaign,
    make_company,
    make_human_decision,
    make_icp_assessment,
    make_research,
    make_user,
)

pytestmark = pytest.mark.django_db

T0 = datetime(2026, 1, 1, 12, tzinfo=UTC)
META: dict[str, Any] = {"model_name": "m1", "prompt_version": "p1", "schema_version": "s1"}


def _violates(obj) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        obj.save()


def _assess(company, fit="strong", **kw):
    make_research(company=company) if not company.research.exists() else None
    return services.record_icp_assessment(company, fit, ["fits"], **META, **kw)


# ------------------------------------------------------------------ ICPAssessment


class TestICPAssessment:
    def test_strong_fit_with_no_signals_is_valid(self):
        """ADR 0008: nothing requires a trigger. No signal table is even involved."""
        company = make_company()
        make_research(company=company)
        a = services.record_icp_assessment(company, "strong", ["Right size"], **META)
        assert a.fit == "strong"
        assert a.concerns == []
        a.full_clean(exclude=["client"])
        related = {str(f.related_model) for f in ICPAssessment._meta.get_fields() if f.is_relation}
        assert not any("signal" in name.lower() for name in related)

    def test_records_profile_version_research_and_client(self):
        company = make_company()
        research = make_research(company=company)
        profile = company.campaign.current_profile
        a = services.record_icp_assessment(
            company, "medium", ["a", " b "], ["c"], raw_output={"x": 1}, **META
        )
        a.refresh_from_db()
        assert a.campaign_profile == profile
        assert a.company_research == research
        assert a.client_id == company.client_id
        assert a.reasons == ["a", "b"]
        assert a.concerns == ["c"]
        assert a.raw_output == {"x": 1}
        assert (a.model_name, a.prompt_version, a.schema_version) == ("m1", "p1", "s1")

    def test_old_assessment_keeps_old_profile_version(self):
        from apps.campaigns.services import create_profile_version

        company = make_company()
        make_research(company=company)
        first = services.record_icp_assessment(company, "strong", ["r"], **META)
        v2 = create_profile_version(company.campaign, {"offer": "A different offer"})
        second = services.record_icp_assessment(company, "weak", ["r"], **META)
        assert first.campaign_profile.version == 1
        assert second.campaign_profile == v2
        assert ICPAssessment.objects.filter(campaign_profile=v2).count() == 1

    def test_history_and_latest(self):
        company = make_company()
        make_research(company=company)
        other = make_company(campaign=company.campaign)
        make_research(company=other)
        old = services.record_icp_assessment(company, "weak", ["r"], created_at=T0, **META)
        new = services.record_icp_assessment(
            company, "strong", ["r"], created_at=T0 + timedelta(days=1), **META
        )
        theirs = services.record_icp_assessment(other, "medium", ["r"], created_at=T0, **META)
        assert ICPAssessment.objects.filter(company=company).count() == 2
        assert ICPAssessment.objects.latest_for(company) == new
        assert ICPAssessment.objects.latest_for(company.pk) == new
        assert set(ICPAssessment.objects.current()) == {new, theirs}
        assert old not in ICPAssessment.objects.current()
        assert ICPAssessment.objects.latest_for(make_company()) is None

    def test_current_tie_breaks_deterministically(self):
        company = make_company()
        make_research(company=company)
        rows = [
            services.record_icp_assessment(company, "weak", ["r"], created_at=T0, **META)
            for _ in range(3)
        ]
        current = list(ICPAssessment.objects.current())
        assert len(current) == 1
        assert current[0] == ICPAssessment.objects.latest_for(company)
        assert current[0] in rows

    def test_requires_research(self):
        with pytest.raises(ValidationError, match="Research"):
            services.record_icp_assessment(make_company(), "strong", ["r"], **META)

    def test_invalid_fit_and_empty_reasons_and_versions(self):
        company = make_company()
        make_research(company=company)
        with pytest.raises(ValidationError):
            services.record_icp_assessment(company, "great", ["r"], **META)
        with pytest.raises(ValidationError):
            services.record_icp_assessment(company, "strong", [" "], **META)
        with pytest.raises(ValidationError):
            services.record_icp_assessment(company, "strong", "just a string", **META)
        with pytest.raises(ValidationError):
            services.record_icp_assessment(
                company, "strong", ["r"], **{**META, "prompt_version": " "}
            )
        assert ICPAssessment.objects.count() == 0

    def test_database_rejects_bad_enum_and_blank_versions(self):
        a = make_icp_assessment()
        bad = ICPAssessment(
            company=a.company,
            campaign_profile=a.campaign_profile,
            company_research=a.company_research,
            fit="great",
            **META,
        )
        _violates(bad)
        blank = ICPAssessment(
            company=a.company,
            campaign_profile=a.campaign_profile,
            company_research=a.company_research,
            fit="weak",
            model_name="",
            prompt_version="p",
            schema_version="s",
        )
        _violates(blank)

    def test_numeric_score_in_output_is_refused(self):
        company = make_company()
        make_research(company=company)
        for raw in ({"score": 8}, {"details": {"Fit_Score": 1}}, {"items": [{"score": 2}]}):
            with pytest.raises(ValidationError, match="score"):
                services.record_icp_assessment(company, "strong", ["r"], raw_output=raw, **META)

    def test_profile_from_other_campaign_or_client_refused(self):
        company = make_company()
        make_research(company=company)
        same_client = make_campaign(client=company.client)
        with pytest.raises(TenantMismatchError):
            services.record_icp_assessment(
                company,
                "strong",
                ["r"],
                campaign_profile=same_client.current_profile,
                **META,
            )
        other_client = make_campaign()
        with pytest.raises(TenantMismatchError):
            services.record_icp_assessment(
                company,
                "strong",
                ["r"],
                campaign_profile=other_client.current_profile,
                **META,
            )

    def test_research_of_other_company_refused(self):
        company = make_company()
        make_research(company=company)
        foreign = make_research(company=make_company(campaign=company.campaign))
        with pytest.raises(TenantMismatchError):
            services.record_icp_assessment(
                company, "strong", ["r"], company_research=foreign, **META
            )

    def test_append_only(self):
        a = make_icp_assessment()
        a.fit = "weak"
        with pytest.raises(ImmutableRecordError):
            a.save()
        with pytest.raises(ImmutableRecordError):
            a.delete()
        with pytest.raises(ImmutableRecordError):
            ICPAssessment.objects.all().update(fit="weak")
        with pytest.raises(ImmutableRecordError):
            ICPAssessment.objects.all().delete()

    def test_profile_and_research_are_protected(self):
        a = make_icp_assessment()
        with pytest.raises((ProtectedError, ImmutableRecordError)):
            a.company_research.delete()
        with pytest.raises((ProtectedError, ImmutableRecordError)):
            a.campaign_profile.delete()

    def test_tenant_scoping(self):
        a = make_icp_assessment()
        b = make_icp_assessment()
        assert list(ICPAssessment.objects.for_client(a.client)) == [a]
        assert b.client_id != a.client_id


# ------------------------------------------------------------------ AIRecommendation


class TestAIRecommendation:
    def test_record_and_latest(self):
        company = make_company()
        a = _assess(company)
        r1 = services.record_ai_recommendation(a, "hold", "Wait", created_at=T0, **META)
        r2 = services.record_ai_recommendation(
            a, "add", " Go ", created_at=T0 + timedelta(hours=1), raw_output={"k": "v"}, **META
        )
        assert r2.explanation == "Go"
        assert r2.company == company
        assert r2.client_id == company.client_id
        assert r2.raw_output == {"k": "v"}
        assert AIRecommendation.objects.latest_for(company) == r2
        assert list(AIRecommendation.objects.current()) == [r2]
        assert r1 in AIRecommendation.objects.filter(company=company)

    def test_invalid_status_and_empty_explanation(self):
        a = make_icp_assessment()
        with pytest.raises(ValidationError):
            services.record_ai_recommendation(a, "maybe", "x", **META)
        with pytest.raises(ValidationError):
            services.record_ai_recommendation(a, "add", "  ", **META)
        with pytest.raises(ValidationError, match="score"):
            services.record_ai_recommendation(a, "add", "x", raw_output={"score": 1}, **META)

    def test_database_constraints(self):
        a = make_icp_assessment()
        _violates(
            AIRecommendation(
                company=a.company, icp_assessment=a, status="maybe", explanation="x", **META
            )
        )
        _violates(
            AIRecommendation(
                company=a.company, icp_assessment=a, status="add", explanation="", **META
            )
        )

    def test_assessment_of_other_company_refused(self):
        a = make_icp_assessment()
        other = make_company(campaign=a.company.campaign)
        rec = AIRecommendation(
            company=other, icp_assessment=a, status="add", explanation="x", **META
        )
        with pytest.raises(TenantMismatchError):
            rec.save()

    def test_append_only(self):
        rec = make_ai_recommendation()
        rec.status = "skip"
        with pytest.raises(ImmutableRecordError):
            rec.save()
        with pytest.raises(ImmutableRecordError):
            AIRecommendation.objects.all().update(status="skip")

    def test_strong_fit_no_signals_can_be_recommended(self):
        a = make_icp_assessment(fit="strong")
        rec = services.record_ai_recommendation(a, "add", "Strong fit", **META)
        assert rec.status == "add"


# ------------------------------------------------------------------ HumanDecision


class TestHumanDecision:
    def test_decision_answers_recommendation_and_leaves_it_untouched(self):
        rec = make_ai_recommendation(status="add")
        before = AIRecommendation.objects.filter(pk=rec.pk).values().get()
        user = make_user()
        for choice in ("skip", "hold", "add"):
            d = services.record_human_decision(
                rec.company, choice, user, ai_recommendation=rec, note=" because "
            )
            assert d.decided_by == user
            assert d.note == "because"
            assert d.client_id == rec.client_id
        assert AIRecommendation.objects.filter(pk=rec.pk).values().get() == before
        assert AIRecommendation.objects.count() == 1
        assert rec.human_decisions.count() == 3

    def test_separate_tables_and_no_shared_columns_for_the_call(self):
        assert AIRecommendation._meta.db_table != HumanDecision._meta.db_table
        ai_fields = {f.name for f in AIRecommendation._meta.get_fields()}
        human_fields = {f.name for f in HumanDecision._meta.get_fields()}
        assert "decision" not in ai_fields
        assert "decided_by" not in ai_fields
        assert "status" not in human_fields

    def test_decision_without_recommendation_is_valid(self):
        company = make_company()
        d = services.record_human_decision(company, "add", make_user())
        assert d.ai_recommendation is None
        d.refresh_from_db()
        assert d.ai_recommendation_id is None

    def test_latest_by_decided_at_and_history_kept(self):
        company = make_company()
        user = make_user()
        first = services.record_human_decision(company, "add", user, decided_at=T0)
        last = services.record_human_decision(
            company, "skip", user, decided_at=T0 + timedelta(days=2)
        )
        # inserted last but decided earlier: not current
        services.record_human_decision(company, "hold", user, decided_at=T0 + timedelta(days=1))
        assert HumanDecision.objects.latest_for(company) == last
        assert list(HumanDecision.objects.current()) == [last]
        assert HumanDecision.objects.filter(company=company).count() == 3
        assert first in HumanDecision.objects.filter(company=company)

    def test_invalid_choice_and_missing_user(self):
        company = make_company()
        with pytest.raises(ValidationError):
            services.record_human_decision(company, "approve", make_user())
        with pytest.raises(ValidationError):
            services.record_human_decision(company, "add", None)  # type: ignore[arg-type]
        _violates(HumanDecision(company=company, decision="approve", decided_by=make_user()))

    def test_recommendation_of_other_company_refused(self):
        rec = make_ai_recommendation()
        other = make_company(campaign=rec.company.campaign)
        with pytest.raises(TenantMismatchError):
            services.record_human_decision(other, "add", make_user(), ai_recommendation=rec)
        with pytest.raises(TenantMismatchError):
            HumanDecision(
                company=other, decision="add", decided_by=make_user(), ai_recommendation=rec
            ).save()
        assert HumanDecision.objects.count() == 0

    def test_recommendation_of_other_client_refused(self):
        rec = make_ai_recommendation()
        other_client_company = make_company()
        with pytest.raises(TenantMismatchError):
            services.record_human_decision(
                other_client_company, "add", make_user(), ai_recommendation=rec
            )

    def test_client_cannot_be_forged(self):
        company = make_company()
        other = make_company()
        with pytest.raises(TenantMismatchError):
            HumanDecision(
                company=company, client=other.client, decision="add", decided_by=make_user()
            ).save()

    def test_append_only(self):
        d = make_human_decision()
        d.decision = "skip"
        with pytest.raises(ImmutableRecordError):
            d.save()
        with pytest.raises(ImmutableRecordError):
            d.delete()
        with pytest.raises(ImmutableRecordError):
            HumanDecision.objects.all().update(note="x")

    def test_user_is_protected(self):
        d = make_human_decision()
        with pytest.raises(ProtectedError):
            d.decided_by.delete()

    def test_tenant_scoping(self):
        d = make_human_decision()
        make_human_decision()
        assert list(HumanDecision.objects.for_client(d.client)) == [d]


# ------------------------------------------------------------------ agreement


class TestAgreement:
    def _decide(self, ai, human, **kw):
        rec = make_ai_recommendation(status=ai, **kw)
        return services.record_human_decision(
            rec.company, human, make_user(), ai_recommendation=rec
        )

    def test_summary(self):
        self._decide("add", "add")
        self._decide("add", "skip")
        self._decide("hold", "hold")
        self._decide("skip", "add")
        services.record_human_decision(make_company(), "add", make_user())
        s = HumanDecision.objects.agreement()
        assert (s.compared, s.agreed, s.disagreed, s.without_ai) == (4, 2, 2, 1)
        assert s.rate == 0.5
        assert s.pairs[("add", "skip")] == 1
        assert s.pairs[("add", "add")] == 1
        assert HumanDecision.objects.agreeing().count() == 2
        overrides = HumanDecision.objects.overrides()
        assert {(o.ai_recommendation.status, o.decision) for o in overrides} == {
            ("add", "skip"),
            ("skip", "add"),
        }

    def test_empty_has_no_rate(self):
        s = HumanDecision.objects.agreement()
        assert (s.compared, s.rate, s.pairs) == (0, None, {})

    def test_current_only_and_client_scope(self):
        rec = make_ai_recommendation(status="add")
        user = make_user()
        services.record_human_decision(
            rec.company, "skip", user, ai_recommendation=rec, decided_at=T0
        )
        services.record_human_decision(
            rec.company,
            "add",
            user,
            ai_recommendation=rec,
            decided_at=T0 + timedelta(days=1),
        )
        self._decide("add", "skip")
        assert HumanDecision.objects.agreement().compared == 3
        current = HumanDecision.objects.current().agreement()
        assert (current.compared, current.agreed) == (2, 1)
        scoped = HumanDecision.objects.for_client(rec.client).agreement()
        assert scoped.compared == 2

    def test_with_ai_status_annotation(self):
        self._decide("hold", "add")
        services.record_human_decision(make_company(), "add", make_user())
        statuses = sorted(str(d.ai_status) for d in HumanDecision.objects.with_ai_status())
        assert statuses == ["None", "hold"]


# ------------------------------------------------------------------ rules across the three


class TestRules:
    NUMERIC = (models.IntegerField, models.FloatField, models.DecimalField)

    @pytest.mark.parametrize("model", [ICPAssessment, AIRecommendation, HumanDecision])
    def test_no_score_or_numeric_fields(self, model):
        for f in model._meta.get_fields():
            assert not isinstance(f, self.NUMERIC), f.name
            assert not any(w in f.name for w in ("score", "rating", "rank", "confidence")), f.name

    @pytest.mark.parametrize("model", [ICPAssessment, AIRecommendation, HumanDecision])
    def test_no_current_flag_and_has_client(self, model):
        names = {f.name for f in model._meta.get_fields()}
        assert not {"is_current", "current"} & names
        assert "client" in names

    @pytest.mark.parametrize("model", [ICPAssessment, AIRecommendation, HumanDecision])
    def test_registered_read_only_in_admin(self, model, rf):
        admin_obj = site._registry[model]
        request = rf.get("/")
        assert admin_obj.has_add_permission(request) is False
        assert admin_obj.has_change_permission(request) is False
        assert admin_obj.has_delete_permission(request) is False
        assert "company" in admin_obj.get_readonly_fields(request)

    def test_full_flow_ai_then_human_disagrees_and_company_keeps_history(self):
        company = make_company()
        make_research(company=company)
        a = services.record_icp_assessment(company, "strong", ["fits"], **META)
        rec = services.record_ai_recommendation(a, "add", "Strong fit", **META)
        services.record_human_decision(company, "hold", make_user(), ai_recommendation=rec)
        assert AIRecommendation.objects.latest_for(company).status == "add"
        assert HumanDecision.objects.latest_for(company).decision == "hold"
