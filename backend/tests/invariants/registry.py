"""The model registry of the invariant suite: which models exist and how to build one of each.

Every guard in this package walks ``project_models()``. A model added to the project without an
entry in ``build_client_rows`` fails ``test_model_registry.py::test_every_model_has_a_builder``
with a message that says what to add. That is the whole point: new models are covered by default
or the build breaks.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db import models

from apps.accounts.models import User
from apps.campaigns.memberships import grant_membership
from apps.campaigns.models import Client
from apps.core.base import AppendOnlyModel, AppendOnlyQuerySet, TenantModel, TenantQuerySet
from apps.core.models import AuditLog
from apps.outreach.services import record_activity
from tests.factories import (
    make_ai_recommendation,
    make_angle,
    make_campaign,
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
from tests.factories_core import make_job, make_job_item


def label(model: type[models.Model]) -> str:
    return model._meta.label


def project_models() -> list[type[models.Model]]:
    """Every model of the project's own apps (``apps.*``), sorted by label."""
    found = [
        m
        for m in apps.get_models()
        if apps.get_app_config(m._meta.app_label).name.startswith("apps.")
    ]
    return sorted(found, key=label)


def is_tenant_model(model: type[models.Model]) -> bool:
    return issubclass(model, TenantModel)


def has_tenant_queryset(model: type[models.Model]) -> bool:
    """True for every model whose manager offers ``for_user`` (tenant models, ``Client``, audit)."""
    return issubclass(type(model._default_manager.all()), TenantQuerySet)


def is_append_only(model: type[models.Model]) -> bool:
    return issubclass(model, AppendOnlyModel)


def has_append_only_queryset(model: type[models.Model]) -> bool:
    return issubclass(type(model._default_manager.all()), AppendOnlyQuerySet)


def build_client_rows(client: Client, member: User | None = None) -> dict[str, Any]:
    """One row of EVERY project model that belongs to ``client``, keyed by model label.

    The graph is consistent (one campaign, one company, research, assessment, recommendation,
    decision, signal, contact, angle, message, ...), so each row can be used as a parent or a
    child in a guard. ``User`` is not tenant data and is not part of the result.
    """
    user = member or make_user()
    campaign = make_campaign(client=client)
    company = make_company(campaign=campaign)
    source = make_data_source(client=client)
    research = make_research(company=company, data_source=source)
    assessment = make_icp_assessment(company=company, company_research=research)
    recommendation = make_ai_recommendation(icp_assessment=assessment)
    decision = make_human_decision(
        company=company, ai_recommendation=recommendation, decided_by=user
    )
    signal = make_signal(company=company, data_source=source)
    contact = make_contact(company=company, data_source=source)
    angle = make_angle(company=company, signals=[signal], data_sources=[source])
    message = make_message(angle=angle, contact=contact, signals=[signal], data_sources=[source])
    activity = record_activity(company, "researched", user)
    job = make_job(client=client, campaign=campaign)
    item = make_job_item(job=job)
    membership = grant_membership(client, user, "viewer")
    audit = AuditLog.objects.filter(client=client).first()
    assert audit is not None, "creating a campaign must write an audit row for its client"

    rows: dict[str, Any] = {
        Client._meta.label: client,
        campaign._meta.label: campaign,
        campaign.current_profile._meta.label: campaign.current_profile,
        membership._meta.label: membership,
        company._meta.label: company,
        source._meta.label: source,
        research._meta.label: research,
        contact._meta.label: contact,
        assessment._meta.label: assessment,
        recommendation._meta.label: recommendation,
        decision._meta.label: decision,
        signal._meta.label: signal,
        angle._meta.label: angle,
        angle.signal_links.get()._meta.label: angle.signal_links.get(),
        angle.source_links.get()._meta.label: angle.source_links.get(),
        message._meta.label: message,
        message.signal_links.get()._meta.label: message.signal_links.get(),
        message.source_links.get()._meta.label: message.source_links.get(),
        activity._meta.label: activity,
        job._meta.label: job,
        item._meta.label: item,
        audit._meta.label: audit,
    }
    return rows


#: Models that are not tenant data (no client of their own). Everything else must be built above.
NOT_TENANT_DATA = {User._meta.label}
