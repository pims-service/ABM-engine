"""Background email steps for approved accounts: find, verify, and both in a row.

Real ``Job`` and ``JobItem`` rows, real connections with encrypted credentials, and fake provider
transports only (no network). The task queue runs in sync mode in tests, so a request finishes
before the API answers, as ``test_provider_tasks`` does.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

import pytest

from apps.companies.enrichment import (
    FIND_JOB_TYPE,
    VERIFY_JOB_TYPE,
    blocker,
    queue_contact_job,
)
from apps.companies.models import Contact
from apps.companies.tasks import verify_contact_emails
from apps.core.jobs import add_items, create_job
from apps.core.models import Job, JobItemStatus, JobStatus
from apps.integrations import services
from apps.integrations.adapters.anthropic.transport import HttpRequest, HttpResponse
from apps.integrations.models import ProviderCall
from tests.factories import (
    make_campaign,
    make_client,
    make_company,
    make_contact,
    make_human_decision,
)
from tests.test_company_api import api_for

KEY = "YOUR_API_KEY"  # pragma: allowlist secret
UID = "a" * 32
LINKEDIN = "https://www.linkedin.com/in/sara-haddad"
REVEALED = {
    "item": UID,
    "status": "success",
    "candidate": {
        "fullName": "Sara Haddad",
        "contacts": [
            {"type": "email", "value": "sara@atlas.example.com", "rating": 100, "subType": "work"}
        ],
    },
}


class FakeProvider:
    """Answers ZeroBounce and SignalHire; ``status`` makes every call fail with that HTTP code."""

    def __init__(self) -> None:
        self.status = 200
        self.verdict: dict[str, Any] = {"status": "valid", "sub_status": "", "mx_found": "true"}
        self.reveal: dict[str, Any] = REVEALED
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if self.status != 200:
            return HttpResponse(self.status, {}, b"{}")
        if "zerobounce" in request.url:
            body: Any = {"Credits": 10} if request.url.endswith("getcredits") else self.verdict
        else:
            body = [self.reveal]
        return HttpResponse(200, {}, json.dumps(body).encode())

    def calls(self, host: str) -> int:
        return sum(host in r.url for r in self.requests)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    shared = FakeProvider()
    for module in ("signalhire", "zerobounce"):
        monkeypatch.setattr(
            f"apps.integrations.adapters.{module}.adapter.UrllibTransport",
            lambda *args, **kwargs: shared,
        )
    return shared


def connect(client: Any, category: str, provider: str) -> None:
    services.create_connection(
        client,
        None,
        category=category,
        provider=provider,
        name=provider,
        config={},
        credentials={"api_key": KEY},
    )


@pytest.fixture
def world(fake: FakeProvider) -> dict[str, Any]:
    client = make_client()
    campaign = make_campaign(client=client)
    company = make_company(campaign=campaign, name="Atlas Freight")
    make_human_decision(company=company, decision="add")
    return {
        "client": client,
        "campaign": campaign,
        "company": company,
        "api": api_for("manager", client),
        "url": f"/api/v1/campaigns/{campaign.pk}/accounts/enrich-contacts/",
    }


def enrich(world: dict[str, Any], step: str, **extra: Any) -> Any:
    return world["api"].post(world["url"], {"step": step, **extra}, format="json")


def contact_of(world: dict[str, Any], **overrides: Any) -> Any:
    overrides.setdefault("role", "primary")
    return make_contact(company=world["company"], **overrides)


# ------------------------------------------------------------------ verify


@pytest.mark.django_db
def test_verifying_runs_in_the_background_and_marks_the_email(world, fake) -> None:
    connect(world["client"], "email_verification", "zerobounce")
    contact = contact_of(world, email="sara@atlas.example.com", email_status="unverified")
    response = enrich(world, "verify_emails")
    assert response.status_code == 202, response.content
    body = response.json()
    assert (body["queued"], body["already_running"], body["not_ready"]) == (1, 0, 0)
    contact.refresh_from_db()
    assert contact.email_status == "verified"
    job = Job.objects.get(pk=body["job_ids"][0])
    assert (job.type, job.status, job.campaign_id) == (
        VERIFY_JOB_TYPE,
        JobStatus.SUCCEEDED,
        world["campaign"].pk,
    )
    item = job.items.get()
    assert (item.status, item.result) == (JobItemStatus.SUCCEEDED, {"verdict": "valid"})


@pytest.mark.django_db
def test_every_provider_call_is_metered(world, fake) -> None:
    connect(world["client"], "email_verification", "zerobounce")
    contact_of(world, email="sara@atlas.example.com", email_status="unverified")
    enrich(world, "verify_emails")
    assert ProviderCall.objects.filter(operation="verify").count() == 1


@pytest.mark.django_db
def test_an_uncertain_answer_stays_unverified(world, fake) -> None:
    connect(world["client"], "email_verification", "zerobounce")
    fake.verdict = {"status": "catch-all"}
    contact = contact_of(world, email="sara@atlas.example.com", email_status="unverified")
    enrich(world, "verify_emails")
    contact.refresh_from_db()
    assert contact.email_status == "unverified"


@pytest.mark.django_db
def test_an_email_that_was_already_checked_is_not_checked_again(world, fake) -> None:
    connect(world["client"], "email_verification", "zerobounce")
    contact_of(world, email="sara@atlas.example.com", email_status="verified")
    body = enrich(world, "verify_emails").json()
    assert (body["queued"], body["not_ready"], body["job_ids"]) == (0, 1, [])
    assert fake.calls("zerobounce") == 0


# ------------------------------------------------------------------ find


@pytest.mark.django_db
def test_finding_saves_the_email_as_unverified(world, fake) -> None:
    connect(world["client"], "email_enrichment", "signalhire")
    contact = contact_of(world, email="", profile_url=LINKEDIN)
    body = enrich(world, "find_emails").json()
    assert body["queued"] == 1
    contact.refresh_from_db()
    assert (contact.email, contact.email_status) == ("sara@atlas.example.com", "unverified")
    assert Job.objects.get(pk=body["job_ids"][0]).type == FIND_JOB_TYPE


@pytest.mark.django_db
def test_a_miss_is_saved_as_not_found(world, fake) -> None:
    connect(world["client"], "email_enrichment", "signalhire")
    fake.reveal = {"item": "x", "status": "failed"}
    contact = contact_of(world, email="", profile_url=LINKEDIN)
    enrich(world, "find_emails")
    contact.refresh_from_db()
    assert (contact.email, contact.email_status) == ("", "not_found")


@pytest.mark.django_db
def test_find_and_verify_checks_each_email_as_soon_as_it_is_found(world, fake) -> None:
    connect(world["client"], "email_enrichment", "signalhire")
    connect(world["client"], "email_verification", "zerobounce")
    contact = contact_of(world, email="", profile_url=LINKEDIN)
    body = enrich(world, "find_and_verify").json()
    assert body["queued"] == 1
    contact.refresh_from_db()
    assert (contact.email, contact.email_status) == ("sara@atlas.example.com", "verified")
    assert sorted(Job.objects.values_list("type", flat=True)) == [FIND_JOB_TYPE, VERIFY_JOB_TYPE]


@pytest.mark.django_db
def test_find_and_verify_covers_people_who_already_have_an_email(world, fake) -> None:
    connect(world["client"], "email_enrichment", "signalhire")
    connect(world["client"], "email_verification", "zerobounce")
    contact_of(world, email="", profile_url=LINKEDIN)
    other = contact_of(
        world, role="secondary", email="omar@atlas.example.com", email_status="unverified"
    )
    body = enrich(world, "find_and_verify").json()
    assert body["queued"] == 2
    other.refresh_from_db()
    assert other.email_status == "verified"
    assert fake.calls("zerobounce") == 2


# ------------------------------------------------------------------ who is covered


@pytest.mark.django_db
def test_only_approved_accounts_and_their_targets_are_covered(world, fake) -> None:
    connect(world["client"], "email_verification", "zerobounce")
    skipped = make_company(campaign=world["campaign"], name="Not Approved")
    make_human_decision(company=skipped, decision="hold")
    make_contact(
        company=skipped, role="primary", email="a@hold.example.com", email_status="unverified"
    )
    contact_of(world, role="none", email="found@atlas.example.com", email_status="unverified")
    body = enrich(world, "verify_emails").json()
    assert (body["queued"], body["not_ready"]) == (0, 0)
    assert fake.calls("zerobounce") == 0


@pytest.mark.django_db
def test_company_ids_narrow_the_request(world, fake) -> None:
    connect(world["client"], "email_verification", "zerobounce")
    contact_of(world, email="sara@atlas.example.com", email_status="unverified")
    other = make_company(campaign=world["campaign"], name="Beta Freight")
    make_human_decision(company=other, decision="add")
    make_contact(
        company=other, role="primary", email="b@beta.example.com", email_status="unverified"
    )
    body = enrich(world, "verify_emails", company_ids=[str(other.pk)]).json()
    assert body["queued"] == 1
    assert Contact.objects.get(email="b@beta.example.com").email_status == "verified"
    assert Contact.objects.get(email="sara@atlas.example.com").email_status == "unverified"


@pytest.mark.django_db
def test_a_person_in_an_open_job_is_not_queued_twice(world, fake) -> None:
    connect(world["client"], "email_verification", "zerobounce")
    contact = contact_of(world, email="sara@atlas.example.com", email_status="unverified")
    waiting = create_job(VERIFY_JOB_TYPE, client=world["client"], campaign=world["campaign"])
    add_items(waiting, [("contact", contact.pk)])
    body = enrich(world, "verify_emails").json()
    assert (body["queued"], body["already_running"]) == (0, 1)
    assert fake.calls("zerobounce") == 0


# ------------------------------------------------------------------ refusals and failures


@pytest.mark.django_db
def test_without_a_connection_the_request_is_refused_with_what_to_add(world) -> None:
    contact_of(world, email="sara@atlas.example.com", email_status="unverified")
    response = enrich(world, "verify_emails")
    assert response.status_code == 400
    assert "ZeroBounce" in response.content.decode()
    assert not Job.objects.exists()


@pytest.mark.django_db
def test_an_unknown_step_is_refused(world) -> None:
    assert enrich(world, "everything").status_code == 400


@pytest.mark.django_db
def test_a_viewer_cannot_start_it(world) -> None:
    viewer = api_for("viewer", world["client"])
    response = viewer.post(world["url"], {"step": "verify_emails"}, format="json")
    assert response.status_code == 403


@pytest.mark.django_db
def test_a_rejected_key_ends_the_job_without_touching_anyone(world, fake) -> None:
    connect(world["client"], "email_verification", "zerobounce")
    fake.status = 401
    contact = contact_of(world, email="sara@atlas.example.com", email_status="unverified")
    # A permanently failed task re-raises in sync mode, as Django-Q2 records it in production.
    with contextlib.suppress(Exception):
        queue_contact_job(
            VERIFY_JOB_TYPE, verify_contact_emails, world["campaign"], None, [contact.pk]
        )
    job = Job.objects.get(type=VERIFY_JOB_TYPE)
    assert job.status == JobStatus.FAILED
    assert "provider_auth" in job.error_summary
    contact.refresh_from_db()
    assert contact.email_status == "unverified"


@pytest.mark.django_db
def test_a_person_who_changed_while_waiting_is_skipped_not_called(world, fake) -> None:
    connect(world["client"], "email_verification", "zerobounce")
    contact = contact_of(world, email="", email_status="unknown")
    job = queue_contact_job(
        VERIFY_JOB_TYPE, verify_contact_emails, world["campaign"], None, [contact.pk]
    )
    assert job.status == JobStatus.SUCCEEDED
    assert "no email" in str(job.items.get().result)
    assert fake.calls("zerobounce") == 0


# ------------------------------------------------------------------ the rule itself


@pytest.mark.django_db
def test_blocker_names_the_reason(world) -> None:
    contact = contact_of(world, email="", profile_url="")
    assert "profile link" in str(blocker(contact, "find"))
    contact.profile_url = LINKEDIN
    assert blocker(contact, "find") is None
    assert "no email" in str(blocker(contact, "verify"))
    contact.email = "sara@atlas.example.com"
    assert "already has an email" in str(blocker(contact, "find"))
    assert blocker(contact, "verify") is None
    contact.email_status = "invalid"
    assert "already checked" in str(blocker(contact, "verify"))
    stranger = make_contact(company=make_company(campaign=world["campaign"]), email="x@y.example")
    assert "not approved" in str(blocker(stranger, "verify"))
