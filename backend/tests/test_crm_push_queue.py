"""Pushing approved accounts to the CRM as a background job (``queue-push-to-crm``).

Same fake HighLevel transport as ``test_crm_push``. The task queue runs in sync mode in tests, so
the job has finished by the time the API answers.
"""

from __future__ import annotations

import contextlib
from typing import Any

import pytest

from apps.core.jobs import add_items, create_job
from apps.core.models import Job, JobStatus
from apps.integrations.crm_connection import NOT_CONFIGURED
from apps.integrations.models import ProviderCall
from apps.outreach.crm_jobs import JOB_TYPE
from apps.outreach.models import Message
from tests.factories import make_campaign, make_client, make_company, make_human_decision
from tests.test_company_api import api_for
from tests.test_crm_push import connect, ready_company
from tests.test_gohighlevel_adapter import FakeHighLevel

pytestmark = [pytest.mark.api, pytest.mark.django_db]


@pytest.fixture
def crm(monkeypatch: pytest.MonkeyPatch) -> FakeHighLevel:
    fake = FakeHighLevel(new=True)
    monkeypatch.setattr(
        "apps.integrations.adapters.gohighlevel.adapter.UrllibTransport",
        lambda *args, **kwargs: fake,
    )
    return fake


@pytest.fixture
def world() -> dict[str, Any]:
    client = make_client()
    campaign = make_campaign(client=client)
    return {
        "client": client,
        "campaign": campaign,
        "company": ready_company(campaign),
        "api": api_for("manager", client),
        "url": f"/api/v1/campaigns/{campaign.pk}/accounts/queue-push-to-crm/",
    }


def queue(world: dict[str, Any], **body: Any) -> Any:
    return world["api"].post(world["url"], body, format="json")


def test_the_push_runs_in_the_background_and_exports_the_messages(world, crm) -> None:
    connect(world["client"])
    response = queue(world)
    assert response.status_code == 202, response.content
    body = response.json()
    assert (body["queued"], body["already_running"], body["not_ready"]) == (1, 0, 0)
    job = Job.objects.get(pk=body["job_id"])
    assert (job.type, job.status, job.campaign_id) == (
        JOB_TYPE,
        JobStatus.SUCCEEDED,
        world["campaign"].pk,
    )
    assert job.items.get().result == {
        "action": "created",
        "remote_id": "cid1",
        "exported_messages": 1,
    }
    assert Message.objects.get(company=world["company"]).status == "exported"
    assert crm.paths() == ["/contacts/upsert", "/contacts/cid1/tags", "/contacts/cid1/notes"]


def test_the_push_is_metered(world, crm) -> None:
    connect(world["client"])
    queue(world)
    call = ProviderCall.objects.get(operation="push_contact")
    assert (call.category, call.provider, call.status) == ("crm", "gohighlevel", "ok")


def test_accounts_that_are_not_ready_are_counted_not_queued(world, crm) -> None:
    connect(world["client"])
    stuck = make_company(campaign=world["campaign"], name="Stuck")
    make_human_decision(company=stuck, decision="add")
    body = queue(world).json()
    assert (body["queued"], body["not_ready"]) == (1, 1)
    assert Job.objects.get(pk=body["job_id"]).items.count() == 1


def test_nothing_ready_queues_no_job(world, crm) -> None:
    connect(world["client"])
    only = make_company(campaign=world["campaign"], name="Only")
    make_human_decision(company=only, decision="add")
    body = queue(world, company_ids=[str(only.pk)]).json()
    assert (body["queued"], body["not_ready"], body["job_id"]) == (0, 1, None)
    assert not Job.objects.exists()


def test_an_account_in_an_open_push_is_not_queued_twice(world, crm) -> None:
    connect(world["client"])
    waiting = create_job(JOB_TYPE, client=world["client"], campaign=world["campaign"])
    add_items(waiting, [("company", world["company"].pk)])
    body = queue(world).json()
    assert (body["queued"], body["already_running"], body["job_id"]) == (0, 1, None)
    assert crm.requests == []


def test_without_a_crm_the_request_is_refused_with_how_to_connect_one(world) -> None:
    response = queue(world)
    assert response.status_code == 400
    assert NOT_CONFIGURED in str(response.json())
    assert not Job.objects.exists()


def test_a_rejected_token_ends_the_job_and_exports_nothing(world, crm) -> None:
    connect(world["client"])
    crm.status = 401
    # A permanently failed task re-raises in sync mode, as Django-Q2 records it in production.
    with contextlib.suppress(Exception):
        queue(world)
    job = Job.objects.get(type=JOB_TYPE)
    assert job.status == JobStatus.FAILED
    assert "provider_auth" in job.error_summary
    assert Message.objects.get(company=world["company"]).status == "approved"


def test_an_account_that_changed_while_waiting_is_skipped_not_pushed(world, crm) -> None:
    from apps.outreach.crm_jobs import SUBJECT_TYPE
    from apps.outreach.tasks import push_accounts_to_crm

    connect(world["client"])
    held = make_company(campaign=world["campaign"], name="Held")
    make_human_decision(company=held, decision="hold")
    job = create_job(JOB_TYPE, client=world["client"], campaign=world["campaign"])
    add_items(job, [(SUBJECT_TYPE, held.pk)])
    push_accounts_to_crm(str(job.pk), [str(held.pk)])
    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED
    assert "ADD" in str(job.items.get().result)
    assert crm.requests == []


@pytest.mark.parametrize(("role", "status"), [("viewer", 403), ("reviewer", 403), ("manager", 202)])
def test_it_needs_the_edit_level(world, crm, role, status) -> None:
    connect(world["client"])
    response = api_for(role, world["client"]).post(world["url"], {}, format="json")
    assert response.status_code == status
