"""Tenant isolation across EVERY client-data endpoint of M1 (issue #53).

The endpoint list is not written by hand. ``endpoints()`` asks DRF's ``EndpointEnumerator`` for
every route in the URLconf. Each route must be one of:

* built on ``ClientScopedMixin`` (then it needs an entry in ``SCENARIOS`` below), or
* listed in ``PUBLIC_ENDPOINTS`` with the reason it carries no client data.

So a new endpoint without an isolation scenario fails ``test_every_endpoint_is_covered`` until
somebody writes one. Scenarios then run the same checks for each: a foreign id is
indistinguishable from a missing id (404, same body), no role gives access across clients,
and the role matrix of docs/permissions.md holds inside a client.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from rest_framework.schemas.generators import EndpointEnumerator
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.campaigns import services
from apps.campaigns.memberships import grant_membership
from apps.campaigns.models import Campaign, CampaignProfile, Client
from apps.companies.models import Company
from apps.core.models import AuditLog, Job
from apps.core.permissions import ClientScopedMixin
from apps.core.roles import Level, role_allows
from apps.core.seed_sample import SeededWorld, load_seeded_world
from apps.imports.models import ImportBatch
from apps.imports.services import create_batch
from apps.integrations import services as services_integrations
from apps.integrations.models import IntegrationConnection, ProviderCall, UsageBudget
from tests.factories import make_campaign, make_client, make_company, make_user
from tests.factories_integrations import make_connection
from tests.fixtures_seed import run_all_seeders
from tests.invariants.conftest import shared_db

# ------------------------------------------------------------------ endpoint inventory

#: Routes that serve no tenant data. A new entry needs a reason.
PUBLIC_ENDPOINTS = {
    "/healthz": "liveness probe",
    "/readyz": "readiness probe",
    "/api/v1/schema/": "OpenAPI document (no data)",
    "/api/v1/docs/": "Swagger UI",
    "/api/v1/redoc/": "ReDoc UI",
    "/api/v1/": "API root (links only)",
    "/api/v1/auth/login/": "authentication",
    "/api/v1/auth/refresh/": "authentication",
    "/api/v1/auth/logout/": "authentication",
    "/api/v1/auth/me/": "the caller's own profile",
    "/api/v1/imports/csv-template/": "static CSV template, no client data (login required)",
}

HTTP = tuple[str, str]  # (METHOD, path template)


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str
    view: type
    action: str

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


def endpoints() -> list[Endpoint]:
    found = []
    for path, method, callback in EndpointEnumerator().get_api_endpoints():
        actions = getattr(callback, "actions", None) or {}
        found.append(Endpoint(method, path, callback.cls, actions.get(method.lower(), "")))
    return found


def scoped_endpoints() -> list[Endpoint]:
    return [e for e in endpoints() if issubclass(e.view, ClientScopedMixin)]


# ------------------------------------------------------------------ the world


ROLES = ["viewer", "reviewer", "manager", "admin"]


@dataclass
class Ctx:
    """Two seeded clients plus an extra one, and everything the scenarios point at."""

    world: SeededWorld
    extra: Client
    users: dict[str, User]  # role -> user, all members of client A (SkyLight) only
    a_client: Client
    a_campaign: Campaign
    a_archived_campaign: Campaign
    a_archived_client: Client  # all role users are members of it
    b_client: Client  # Meridian
    b_campaign: Campaign
    b_archived_campaign: Campaign
    b_archived_client: Client
    extra_campaign: Campaign
    a_batch: ImportBatch
    b_batch: ImportBatch
    extra_batch: ImportBatch
    a_company: Company
    a_archived_company: Company
    b_company: Company
    b_archived_company: Company
    extra_company: Company
    extra_archived_company: Company
    a_connection: IntegrationConnection
    a_archived_connection: IntegrationConnection
    b_connection: IntegrationConnection
    b_archived_connection: IntegrationConnection
    extra_connection: IntegrationConnection
    outsider: User = field(default=None)  # type: ignore[assignment]

    def api(self, user: User | None) -> APIClient:
        client = APIClient()
        if user is not None:
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
        return client


@pytest.fixture(scope="module")
def _ctx_module(django_db_setup: None, django_db_blocker: Any) -> Iterator[Ctx]:
    """Seed once per module (seeding is the slow part); every test rolls back its own changes."""
    with shared_db(django_db_blocker):
        run_all_seeders()
        yield build_ctx(load_seeded_world())


@pytest.fixture
def ctx(_ctx_module: Ctx, db: None) -> Ctx:
    return _ctx_module


def build_ctx(world: SeededWorld) -> Ctx:
    a, b = world.clients["skylight"], world.clients["meridian"]
    users = {role: world.users[f"skylight_{role}"] for role in ROLES}

    a_arch = make_campaign(client=a, name="A archived")
    services.archive_campaign(a_arch)
    b_arch = make_campaign(client=b, name="B archived")
    services.archive_campaign(b_arch)

    a_arch_client = make_client(name="A archived client")
    for role, user in users.items():
        grant_membership(a_arch_client, user, role)
    services.archive_client(a_arch_client)
    b_arch_client = make_client(name="B archived client")
    services.archive_client(b_arch_client)

    extra = make_client(name="Extra Co")
    extra_campaign = make_campaign(client=extra, name="Extra campaign")
    companies = {}
    for key, campaign in (
        ("a", world.campaigns["skylight"]),
        ("b", world.campaigns["meridian"]),
        ("extra", extra_campaign),
    ):
        companies[key] = make_company(campaign=campaign, name=f"Probe {key}")
        archived = make_company(campaign=campaign, name=f"Probe archived {key}")
        archived.archive()
        companies[f"{key}_archived"] = archived
    a_conn_archived = make_connection(a, name="A archived connection")
    services_integrations.archive_connection(a_conn_archived)
    b_conn_archived = make_connection(b, name="B archived connection")
    services_integrations.archive_connection(b_conn_archived)
    return Ctx(
        world=world,
        extra=extra,
        users=users,
        a_client=a,
        a_campaign=world.campaigns["skylight"],
        a_archived_campaign=a_arch,
        a_archived_client=a_arch_client,
        b_client=b,
        b_campaign=world.campaigns["meridian"],
        b_archived_campaign=b_arch,
        b_archived_client=b_arch_client,
        extra_campaign=extra_campaign,
        a_batch=create_batch(world.campaigns["skylight"], "provider", total_count=1),
        b_batch=create_batch(world.campaigns["meridian"], "provider", total_count=1),
        extra_batch=create_batch(extra_campaign, "provider", total_count=1),
        a_company=companies["a"],
        a_archived_company=companies["a_archived"],
        b_company=companies["b"],
        b_archived_company=companies["b_archived"],
        extra_company=companies["extra"],
        extra_archived_company=companies["extra_archived"],
        a_connection=make_connection(a, name="A connection"),
        a_archived_connection=a_conn_archived,
        b_connection=make_connection(b, name="B connection"),
        b_archived_connection=b_conn_archived,
        extra_connection=make_connection(extra, name="Extra connection"),
        outsider=make_user(),
    )


@contextmanager
def rolled_back() -> Iterator[None]:
    """Run a request, then undo every database change it made (savepoint rollback)."""
    with transaction.atomic():
        yield
        transaction.set_rollback(True)


# ------------------------------------------------------------------ scenarios

PROFILE = {"offer": "Probe offer"}
PROBE_SECRET = "ISOLATIONPROBESECRET1234567"  # pragma: allowlist secret
SEQ = iter(range(10_000))


def unique(prefix: str) -> str:
    return f"{prefix} {next(SEQ)} {uuid.uuid4().hex[:6]}"


@dataclass(frozen=True)
class Scenario:
    """How to exercise one endpoint.

    ``kind``: ``list`` (collection read), ``item`` (``{pk}`` in the path), ``nested``
    (``{client_pk}`` in the path), ``campaign_nested`` (``{campaign_pk}`` in the path),
    ``create`` (client id in the body), ``client_create`` (no client involved).
    ``target`` picks the object family for ``{pk}``: ``client``, ``campaign``, ``company`` or
    ``connection``. ``archived`` says the target must be archived (restore). ``level`` is what
    docs/permissions.md demands.
    """

    key: str
    kind: str
    level: Level
    target: str = ""
    archived: bool = False
    body: Callable[[], dict[str, Any]] | None = None


def _scenarios() -> dict[str, Scenario]:
    items = [
        Scenario("GET /api/v1/clients/", "list", Level.READ),
        Scenario(
            "POST /api/v1/clients/",
            "client_create",
            Level.MANAGE,
            body=lambda: {"name": unique("C")},
        ),
        Scenario("GET /api/v1/clients/{pk}/", "item", Level.READ, "client"),
        Scenario(
            "PUT /api/v1/clients/{pk}/",
            "item",
            Level.EDIT,
            "client",
            body=lambda: {"name": unique("Renamed")},
        ),
        Scenario(
            "PATCH /api/v1/clients/{pk}/",
            "item",
            Level.EDIT,
            "client",
            body=lambda: {"notes": "tampered"},
        ),
        Scenario("POST /api/v1/clients/{pk}/archive/", "item", Level.MANAGE, "client"),
        Scenario(
            "POST /api/v1/clients/{pk}/restore/", "item", Level.MANAGE, "client", archived=True
        ),
        Scenario("GET /api/v1/clients/{client_pk}/campaigns/", "nested", Level.READ),
        Scenario(
            "POST /api/v1/clients/{client_pk}/campaigns/",
            "nested",
            Level.EDIT,
            body=lambda: {"name": unique("Nested"), "profile": PROFILE},
        ),
        Scenario("GET /api/v1/campaigns/", "list", Level.READ),
        Scenario("POST /api/v1/campaigns/", "create", Level.EDIT),
        Scenario("GET /api/v1/campaigns/{pk}/", "item", Level.READ, "campaign"),
        Scenario(
            "PUT /api/v1/campaigns/{pk}/",
            "item",
            Level.EDIT,
            "campaign",
            body=lambda: {"name": unique("Put"), "profile": {"offer": "Changed offer"}},
        ),
        Scenario(
            "PATCH /api/v1/campaigns/{pk}/",
            "item",
            Level.EDIT,
            "campaign",
            body=lambda: {"profile": {"offer": "Patched offer"}},
        ),
        Scenario("POST /api/v1/campaigns/{pk}/activate/", "item", Level.EDIT, "campaign"),
        Scenario("POST /api/v1/campaigns/{pk}/archive/", "item", Level.MANAGE, "campaign"),
        Scenario(
            "POST /api/v1/campaigns/{pk}/restore/", "item", Level.MANAGE, "campaign", archived=True
        ),
        Scenario(
            "POST /api/v1/campaigns/{pk}/clone/",
            "item",
            Level.EDIT,
            "campaign",
            body=lambda: {"name": unique("Clone")},
        ),
        Scenario("GET /api/v1/campaigns/{pk}/profile-versions/", "item", Level.READ, "campaign"),
        Scenario(
            "GET /api/v1/campaigns/{pk}/profile-versions/{version}/", "item", Level.READ, "campaign"
        ),
        Scenario("GET /api/v1/campaigns/{pk}/rules-summary/", "item", Level.READ, "campaign"),
        # Provider import (issue #67)
        Scenario("GET /api/v1/providers/", "list", Level.READ),
        Scenario(
            "POST /api/v1/campaigns/{pk}/provider-search/",
            "item",
            Level.EDIT,
            "campaign",
            body=lambda: {"provider": "fake"},
        ),
        Scenario(
            "POST /api/v1/campaigns/{pk}/provider-import/",
            "item",
            Level.EDIT,
            "campaign",
            body=lambda: {"provider": "fake", "companies": [{"name": unique("Probe")}]},
        ),
        Scenario("GET /api/v1/provider-imports/{pk}/", "item", Level.READ, "batch"),
        Scenario(
            "POST /api/v1/campaigns/{campaign_id}/csv-import/preview/",
            "item",
            Level.EDIT,
            "campaign",
            body=lambda: csv_upload(),
        ),
        Scenario(
            "POST /api/v1/campaigns/{campaign_id}/csv-import/",
            "item",
            Level.EDIT,
            "campaign",
            body=lambda: csv_upload(),
        ),
        Scenario("POST /api/v1/imports/{batch_id}/cancel/", "item", Level.EDIT, "batch"),
        Scenario("GET /api/v1/imports/{batch_id}/", "item", Level.READ, "batch"),
        Scenario("GET /api/v1/imports/{batch_id}/rows/", "item", Level.READ, "batch"),
        Scenario("GET /api/v1/imports/{batch_id}/errors.csv", "item", Level.READ, "batch"),
        Scenario("GET /api/v1/campaigns/{campaign_id}/imports/", "item", Level.READ, "campaign"),
        Scenario("GET /api/v1/campaigns/{campaign_pk}/companies/", "campaign_nested", Level.READ),
        Scenario("GET /api/v1/campaigns/{campaign_pk}/accounts/", "campaign_nested", Level.READ),
        Scenario(
            "GET /api/v1/campaigns/{campaign_pk}/accounts/export/", "campaign_nested", Level.EDIT
        ),
        Scenario(
            "GET /api/v1/campaigns/{campaign_pk}/accounts/summary/", "campaign_nested", Level.READ
        ),
        Scenario(
            "POST /api/v1/campaigns/{campaign_pk}/accounts/analyze/",
            "campaign_nested",
            Level.EDIT,
            body=lambda: {"scope": "unassessed"},
        ),
        Scenario(
            "POST /api/v1/campaigns/{campaign_pk}/companies/",
            "campaign_nested",
            Level.EDIT,
            body=lambda: {"name": unique("Probe"), "website": f"{uuid.uuid4().hex}.example.com"},
        ),
        Scenario("GET /api/v1/companies/{pk}/", "item", Level.READ, "company"),
        Scenario("POST /api/v1/companies/{pk}/analyze/", "item", Level.EDIT, "company"),
        Scenario("POST /api/v1/companies/{pk}/website-research/", "item", Level.EDIT, "company"),
        Scenario("GET /api/v1/companies/{pk}/card/", "item", Level.READ, "company"),
        Scenario("GET /api/v1/companies/{pk}/activity/", "item", Level.READ, "company"),
        Scenario("GET /api/v1/companies/{pk}/contacts/", "item", Level.READ, "company"),
        Scenario("GET /api/v1/companies/{pk}/messages/", "item", Level.READ, "company"),
        Scenario(
            "POST /api/v1/companies/{pk}/outreach-status/",
            "item",
            Level.EDIT,
            "company",
            body=lambda: {"stage": "contacted"},
        ),
        Scenario(
            "POST /api/v1/companies/{pk}/generate-messages/",
            "item",
            Level.EDIT,
            "company",
            body=lambda: {"channels": ["email"], "language": "en"},
        ),
        Scenario(
            "POST /api/v1/companies/{pk}/approve-message/",
            "item",
            Level.DECIDE,
            "company",
            body=lambda: {"message_id": str(uuid.uuid4())},
        ),
        Scenario(
            "POST /api/v1/companies/{pk}/edit-message/",
            "item",
            Level.DECIDE,
            "company",
            body=lambda: {"message_id": str(uuid.uuid4()), "body": "x"},
        ),
        Scenario(
            "POST /api/v1/companies/{pk}/export-message/",
            "item",
            Level.DECIDE,
            "company",
            body=lambda: {"message_id": str(uuid.uuid4())},
        ),
        Scenario(
            "POST /api/v1/companies/{pk}/add-contact/",
            "item",
            Level.EDIT,
            "company",
            body=lambda: {"name": "Probe Person"},
        ),
        Scenario(
            "POST /api/v1/companies/{pk}/update-contact/",
            "item",
            Level.EDIT,
            "company",
            body=lambda: {"contact_id": str(uuid.uuid4()), "title": "x"},
        ),
        Scenario(
            "POST /api/v1/companies/{pk}/archive-contact/",
            "item",
            Level.EDIT,
            "company",
            body=lambda: {"contact_id": str(uuid.uuid4())},
        ),
        Scenario(
            "POST /api/v1/companies/{pk}/feedback/",
            "item",
            Level.DECIDE,
            "company",
            body=lambda: {"kind": "not_b2b"},
        ),
        Scenario(
            "POST /api/v1/companies/{pk}/decision/",
            "item",
            Level.DECIDE,
            "company",
            body=lambda: {"decision": "hold"},
        ),
        Scenario("POST /api/v1/companies/{pk}/archive/", "item", Level.MANAGE, "company"),
        Scenario(
            "POST /api/v1/companies/{pk}/restore/", "item", Level.MANAGE, "company", archived=True
        ),
        # Integration connections (issue #76): credentials are sensitive, so reading needs EDIT
        # (manager, admin) and every write needs MANAGE (admin).
        Scenario("GET /api/v1/integrations/connections/", "list", Level.EDIT),
        Scenario(
            "POST /api/v1/integrations/connections/",
            "create",
            Level.MANAGE,
            body=lambda: {
                "category": "llm",
                "provider": "fake",
                "name": unique("Conn"),
                "credentials": {"api_key": PROBE_SECRET},
            },
        ),
        Scenario("GET /api/v1/integrations/connections/{pk}/", "item", Level.EDIT, "connection"),
        Scenario(
            "PUT /api/v1/integrations/connections/{pk}/",
            "item",
            Level.MANAGE,
            "connection",
            body=lambda: {"name": unique("Put")},
        ),
        Scenario(
            "PATCH /api/v1/integrations/connections/{pk}/",
            "item",
            Level.MANAGE,
            "connection",
            body=lambda: {"name": unique("Patch")},
        ),
        Scenario(
            "POST /api/v1/integrations/connections/{pk}/enable/",
            "item",
            Level.MANAGE,
            "connection",
        ),
        Scenario(
            "POST /api/v1/integrations/connections/{pk}/disable/",
            "item",
            Level.MANAGE,
            "connection",
        ),
        Scenario(
            "POST /api/v1/integrations/connections/{pk}/archive/",
            "item",
            Level.MANAGE,
            "connection",
        ),
        Scenario(
            "POST /api/v1/integrations/connections/{pk}/restore/",
            "item",
            Level.MANAGE,
            "connection",
            archived=True,
        ),
        Scenario(
            "POST /api/v1/integrations/connections/{pk}/test/",
            "item",
            Level.MANAGE,
            "connection",
        ),
        # Usage and budgets (issue #79): reading needs EDIT, setting a budget needs MANAGE.
        Scenario("GET /api/v1/integrations/usage/", "list", Level.EDIT),
        Scenario("GET /api/v1/integrations/usage/summary/", "list", Level.EDIT),
        Scenario(
            "GET /api/v1/integrations/connections/{pk}/budget/", "item", Level.EDIT, "connection"
        ),
        Scenario(
            "PUT /api/v1/integrations/connections/{pk}/budget/",
            "item",
            Level.MANAGE,
            "connection",
            body=lambda: {"credit_limit": "50", "warn_at_percent": 90},
        ),
    ]
    return {s.key: s for s in items}


def csv_upload() -> dict[str, Any]:
    """A multipart body: ``send`` switches to multipart when a value is a file."""
    content = b"company_name,website\nProbe Co,probe.example.com\n"
    return {"file": SimpleUploadedFile("probe.csv", content, content_type="text/csv")}


SCENARIOS = _scenarios()
SCENARIO_IDS = list(SCENARIOS)


def fill(path: str, **values: str) -> str:
    out = path.replace("{version}", "1")
    if "pk" in values:  # routes whose id is named after the object
        out = out.replace("{campaign_id}", values["pk"]).replace("{batch_id}", values["pk"])
    for name, value in values.items():
        out = out.replace("{" + name + "}", value)
    return out


def own_target(ctx: Ctx, s: Scenario) -> str:
    if s.target == "client":
        return str((ctx.a_archived_client if s.archived else ctx.a_client).pk)
    if s.target == "batch":
        return str(ctx.a_batch.pk)
    if s.target == "company":
        return str((ctx.a_archived_company if s.archived else ctx.a_company).pk)
    if s.target == "connection":
        return str((ctx.a_archived_connection if s.archived else ctx.a_connection).pk)
    return str((ctx.a_archived_campaign if s.archived else ctx.a_campaign).pk)


def foreign_target(ctx: Ctx, s: Scenario) -> str:
    if s.target == "client":
        return str((ctx.b_archived_client if s.archived else ctx.b_client).pk)
    if s.target == "batch":
        return str(ctx.b_batch.pk)
    if s.target == "company":
        return str((ctx.b_archived_company if s.archived else ctx.b_company).pk)
    if s.target == "connection":
        return str((ctx.b_archived_connection if s.archived else ctx.b_connection).pk)
    return str((ctx.b_archived_campaign if s.archived else ctx.b_campaign).pk)


def request_for(
    ctx: Ctx, s: Scenario, *, foreign: bool, client_id: Any = None
) -> tuple[str, str, Any]:
    """(method, url, body) for ``s`` aimed at B's objects (``foreign``) or A's own."""
    method, template = s.key.split(" ", 1)
    body = s.body() if s.body else None
    if s.kind == "item":
        pk = foreign_target(ctx, s) if foreign else own_target(ctx, s)
        return method, fill(template, pk=pk), body
    if s.kind == "nested":
        cid = client_id or (ctx.b_client.pk if foreign else ctx.a_client.pk)
        return method, fill(template, client_pk=str(cid)), body
    if s.kind == "campaign_nested":
        cid = client_id or (ctx.b_campaign.pk if foreign else ctx.a_campaign.pk)
        return method, fill(template, campaign_pk=str(cid)), body
    if s.kind == "create":
        cid = client_id or (ctx.b_client.pk if foreign else ctx.a_client.pk)
        if s.body:
            return method, template, {**s.body(), "client": str(cid)}
        return method, template, {"client": str(cid), "name": unique("New"), "profile": PROFILE}
    return method, template, body


def send(api: APIClient, method: str, url: str, body: Any) -> Any:
    verb = getattr(api, method.lower())
    if body is None:
        return verb(url)
    uploading = any(hasattr(value, "read") for value in body.values())
    return verb(url, body, format="multipart" if uploading else "json")


def normalized(response: Any) -> Any:
    """The JSON body without the per-request id, for "same answer" comparisons."""
    data = json.loads(response.content or b"null")
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        data["error"] = {k: v for k, v in data["error"].items() if k != "request_id"}
    return data


def tenant_state(*clients: Client) -> dict[str, Any]:
    """What a stranger's request must not be able to change in these clients."""
    state: dict[str, Any] = {}
    for client in clients:
        state[str(client.pk)] = {
            "client": dict(Client.objects.filter(pk=client.pk).values().get()),
            "campaigns": sorted(
                map(
                    str,
                    Campaign.objects.filter(client=client).values_list(
                        "pk", "name", "status", "archived_at", "current_profile_id"
                    ),
                )
            ),
            "profiles": CampaignProfile.objects.filter(client=client).count(),
            "audit": AuditLog.objects.filter(client=client).count(),
            "budgets": UsageBudget.objects.filter(client=client).count(),
            "calls": ProviderCall.objects.filter(client=client).count(),
            "companies": sorted(
                map(
                    str,
                    Company.objects.filter(client=client).values_list(
                        "pk", "name", "status", "archived_at"
                    ),
                )
            ),
            "jobs": Job.objects.filter(client=client).count(),
            "import_batches": ImportBatch.objects.filter(client=client).count(),
            "connections": sorted(
                map(
                    str,
                    IntegrationConnection.objects.filter(client=client).values_list(
                        "pk", "name", "status", "archived_at", "credentials_updated_at"
                    ),
                )
            ),
        }
    return state


# ------------------------------------------------------------------ coverage of the URLconf


def test_the_url_walk_finds_the_m1_endpoints() -> None:
    keys = {e.key for e in scoped_endpoints()}
    assert len(keys) >= 20, "the endpoint enumeration returned too little"
    assert "GET /api/v1/campaigns/{pk}/" in keys


def test_every_endpoint_is_either_public_with_a_reason_or_client_scoped() -> None:
    unclassified = [
        e.key
        for e in endpoints()
        if not issubclass(e.view, ClientScopedMixin) and e.path not in PUBLIC_ENDPOINTS
    ]
    assert not unclassified, (
        f"{unclassified} serve data but are not built on ClientScopedViewSet / "
        "ClientScopedModelViewSet (apps/core/permissions.py). Use them, or add the path to "
        "PUBLIC_ENDPOINTS in tests/invariants/test_tenant_isolation_api.py with a reason."
    )


def test_every_endpoint_is_covered() -> None:
    """A new endpoint without an isolation scenario fails here; so does a removed route."""
    routed = {e.key for e in scoped_endpoints()}
    missing = routed - set(SCENARIOS)
    stale = set(SCENARIOS) - routed
    assert not missing, (
        f"No isolation scenario for {sorted(missing)}. Add a Scenario(...) to SCENARIOS in "
        "tests/invariants/test_tenant_isolation_api.py (see the docstring there)."
    )
    assert not stale, f"Scenarios for routes that no longer exist: {sorted(stale)}"


def test_scenario_levels_match_the_levels_the_views_declare() -> None:
    """The role matrix below is written from docs/permissions.md, not read from the views."""
    for endpoint in scoped_endpoints():
        declared = endpoint.view.action_levels  # type: ignore[attr-defined]
        assert endpoint.action in declared, (
            f"{endpoint.key}: action '{endpoint.action}' has no entry in action_levels "
            "(it would silently fall back to READ/MANAGE)"
        )
        assert declared[endpoint.action] == SCENARIOS[endpoint.key].level, endpoint.key


def test_no_endpoint_allows_delete() -> None:
    assert not [e.key for e in endpoints() if e.method == "DELETE"]


# ------------------------------------------------------------------ foreign ids are "not found"

ITEM_LIKE = [
    k for k, s in SCENARIOS.items() if s.kind in {"item", "nested", "campaign_nested", "create"}
]


@pytest.mark.parametrize("key", ITEM_LIKE)
@pytest.mark.parametrize("role", [*ROLES])
def test_another_clients_object_is_indistinguishable_from_a_missing_one(ctx, key, role) -> None:
    """For every role of client A (admin included), client B's id answers exactly like an id that
    does not exist: 404 with the same body, and nothing in B changes."""
    s = SCENARIOS[key]
    api = ctx.api(ctx.users[role])
    before = tenant_state(ctx.b_client, ctx.extra, ctx.b_archived_client)

    method, url, body = request_for(ctx, s, foreign=True)
    missing_id = uuid.uuid4()
    if s.kind == "item":
        m_url, m_body = url.replace(foreign_target(ctx, s), str(missing_id)), body
    elif s.kind == "nested":
        m_url, m_body = url.replace(str(ctx.b_client.pk), str(missing_id)), body
    elif s.kind == "campaign_nested":
        m_url, m_body = url.replace(str(ctx.b_campaign.pk), str(missing_id)), body
    else:
        _, m_url, m_body = request_for(ctx, s, foreign=True, client_id=missing_id)
        body = {**m_body, "client": str(ctx.b_client.pk)}
    with rolled_back():
        foreign = send(api, method, url, body)
    with rolled_back():
        missing = send(api, method, m_url, m_body)

    # Creating needs the level in SOME client first: a role that never reaches it is refused
    # up front (403) whatever client is named, which is still the same answer for B and "none".
    early_403 = s.kind in {"create", "nested", "campaign_nested"} and not role_allows(role, s.level)
    expected = 403 if early_403 else 404
    assert missing.status_code == expected, (key, role, missing.content)
    assert foreign.status_code == expected, (key, role, foreign.content)
    assert normalized(foreign) == normalized(missing)
    assert tenant_state(ctx.b_client, ctx.extra, ctx.b_archived_client) == before


@pytest.mark.parametrize("key", [k for k, s in SCENARIOS.items() if s.kind == "item"])
def test_a_third_clients_object_is_not_found_either(ctx, key) -> None:
    """A user of client A cannot reach a client they have never heard of (not only Meridian)."""
    s = SCENARIOS[key]
    api = ctx.api(ctx.users["admin"])
    target: Any = {
        "client": ctx.extra,
        "campaign": ctx.extra_campaign,
        "batch": ctx.extra_batch,
        "connection": ctx.extra_connection,
        "company": ctx.extra_archived_company if s.archived else ctx.extra_company,
    }[s.target]
    if s.archived and s.target not in {"company", "batch"}:
        archive: Any = {
            "client": services.archive_client,
            "campaign": services.archive_campaign,
            "connection": services_integrations.archive_connection,
        }[s.target]
        archive(target)
    method, template = key.split(" ", 1)
    url = fill(template, pk=str(target.pk))
    with rolled_back():
        response = send(api, method, url, s.body() if s.body else None)
    assert response.status_code == 404


@pytest.mark.parametrize("role", [*ROLES])
def test_a_user_with_no_membership_anywhere_gets_404_or_an_empty_list(ctx, role) -> None:
    api = ctx.api(ctx.outsider)
    for key, s in SCENARIOS.items():
        if s.kind == "item":
            method, url, body = request_for(ctx, s, foreign=False)
            with rolled_back():
                assert send(api, method, url, body).status_code == 404, key
    for url in ("/api/v1/clients/", "/api/v1/campaigns/"):
        data = api.get(url + "?archived=all").json()
        assert data["results"] == []


# ------------------------------------------------------------------ lists never leak


def visible_ids(api: APIClient, url: str) -> set[str]:
    out: set[str] = set()
    while url:
        response = api.get(url)
        assert response.status_code == 200, response.content
        data = response.json()
        out |= {row["id"] for row in data["results"]}
        url = data.get("next") or ""
        url = url.replace("http://testserver", "")
    return out


@pytest.mark.parametrize("role", [*ROLES])
def test_lists_only_contain_the_users_own_client(ctx, role) -> None:
    api = ctx.api(ctx.users[role])
    foreign_clients = {str(c.pk) for c in (ctx.b_client, ctx.extra, ctx.b_archived_client)}
    foreign_campaigns = {
        str(c.pk) for c in (ctx.b_campaign, ctx.b_archived_campaign, ctx.extra_campaign)
    }
    clients = visible_ids(api, "/api/v1/clients/?archived=all&page_size=100")
    campaigns = visible_ids(api, "/api/v1/campaigns/?archived=all&page_size=100")
    assert str(ctx.a_client.pk) in clients
    assert str(ctx.a_campaign.pk) in campaigns
    assert not clients & foreign_clients
    assert not campaigns & foreign_campaigns
    for query in ("search=Meridian", "ordering=name", "status=active"):
        assert not visible_ids(api, f"/api/v1/campaigns/?{query}&archived=all") & foreign_campaigns
        assert not visible_ids(api, f"/api/v1/clients/?{query}&archived=all") & foreign_clients


@pytest.mark.parametrize("role", [*ROLES])
def test_company_lists_only_contain_the_users_own_campaign(ctx, role) -> None:
    api = ctx.api(ctx.users[role])
    own = visible_ids(api, f"/api/v1/campaigns/{ctx.a_campaign.pk}/companies/?archived=all")
    assert str(ctx.a_company.pk) in own
    foreign = {
        str(c.pk)
        for c in (
            ctx.b_company,
            ctx.b_archived_company,
            ctx.extra_company,
            ctx.extra_archived_company,
        )
    }
    assert not own & foreign
    for query in ("search=Probe", "ordering=name", "possible_duplicate=false"):
        url = f"/api/v1/campaigns/{ctx.a_campaign.pk}/companies/?{query}&archived=all"
        assert not visible_ids(api, url) & foreign


@pytest.mark.parametrize("role", [*ROLES])
def test_filtering_by_another_clients_id_returns_nothing(ctx, role) -> None:
    api = ctx.api(ctx.users[role])
    for client in (ctx.b_client, ctx.extra):
        response = api.get(f"/api/v1/campaigns/?client={client.pk}&archived=all")
        assert response.status_code == 200
        assert response.json()["results"] == []


def test_global_admin_sees_every_client(ctx) -> None:
    api = ctx.api(make_user(is_superuser=True))
    clients = visible_ids(api, "/api/v1/clients/?archived=all&page_size=100")
    assert {str(c.pk) for c in (ctx.a_client, ctx.b_client, ctx.extra)} <= clients


def test_me_exposes_only_the_callers_own_data(ctx) -> None:
    body = ctx.api(ctx.users["viewer"]).get("/api/v1/auth/me/").json()
    text = json.dumps(body)
    assert str(ctx.b_client.pk) not in text
    assert ctx.users["viewer"].email in text
    assert ctx.world.users["meridian_admin"].email not in text


# ------------------------------------------------------------------ role matrix inside a client


def expected_for(role: str, level: Level) -> bool:
    return role == "global" or role_allows(role, level)


def run_matrix(ctx: Ctx, key: str, api: APIClient, allowed: bool) -> None:
    s = SCENARIOS[key]
    method, url, body = request_for(ctx, s, foreign=False)
    with rolled_back():
        response = send(api, method, url, body)
    if allowed:
        assert response.status_code not in (401, 403, 404), (
            key,
            response.status_code,
            response.content,
        )
    else:
        assert response.status_code == 403, (key, response.status_code, response.content)


@pytest.mark.parametrize("key", SCENARIO_IDS)
def test_role_matrix_inside_the_users_own_client(ctx, key) -> None:
    s = SCENARIOS[key]
    for role in ROLES:
        run_matrix(ctx, key, ctx.api(ctx.users[role]), expected_for(role, s.level))
    run_matrix(ctx, key, ctx.api(make_user(is_superuser=True)), True)


@pytest.mark.parametrize("key", SCENARIO_IDS)
def test_anonymous_requests_get_401(ctx, key) -> None:
    s = SCENARIOS[key]
    method, url, body = request_for(ctx, s, foreign=False)
    with rolled_back():
        response = send(ctx.api(None), method, url, body)
    assert response.status_code == 401, (key, response.content)
    assert normalized(response)["error"]["code"] == "not_authenticated"


@pytest.mark.parametrize("key", SCENARIO_IDS)
@pytest.mark.parametrize("who", ["admin", "global"])
def test_an_inactive_user_gets_nothing_even_with_a_valid_token(ctx, key, who) -> None:
    user = ctx.users["admin"] if who == "admin" else make_user(is_superuser=True)
    api = ctx.api(user)  # the token is issued while the user is still active
    User.objects.filter(pk=user.pk).update(is_active=False)
    s = SCENARIOS[key]
    method, url, body = request_for(ctx, s, foreign=False)
    with rolled_back():
        assert send(api, method, url, body).status_code == 401, key


@pytest.mark.parametrize(
    "key", [k for k, s in SCENARIOS.items() if s.kind in {"item", "nested", "campaign_nested"}]
)
def test_roles_are_per_client_not_global(ctx, key) -> None:
    """Admin in A and only viewer in B: B's answer follows the viewer role, A's the admin role."""
    s = SCENARIOS[key]
    user = make_user()
    grant_membership(ctx.a_client, user, "admin")
    grant_membership(ctx.b_client, user, "viewer")
    api = ctx.api(user)
    # (an archived client cannot be granted to, so its restore has no "viewer in B" variant)
    if not (s.target == "client" and s.archived):
        method, url, body = request_for(ctx, s, foreign=True)  # aimed at B
        with rolled_back():
            response = send(api, method, url, body)
        if role_allows("viewer", s.level):
            assert response.status_code not in (401, 403, 404), (key, response.content)
        else:
            assert response.status_code == 403, (key, response.content)
    method, url, body = request_for(ctx, s, foreign=False)  # aimed at A, where they are admin
    if s.target == "client" and s.archived:
        api = ctx.api(ctx.users["admin"])  # the archived client was set up with the role users
    with rolled_back():
        assert send(api, method, url, body).status_code not in (401, 403, 404), key


def test_a_revoked_membership_loses_access_on_the_next_request(ctx) -> None:
    from apps.campaigns.memberships import revoke_membership

    user = make_user()
    grant_membership(ctx.a_client, user, "manager")
    api = ctx.api(user)
    url = f"/api/v1/campaigns/{ctx.a_campaign.pk}/"
    assert api.get(url).status_code == 200
    revoke_membership(ctx.a_client, user)
    assert api.get(url).status_code == 404


def test_a_cloned_campaign_stays_in_the_source_clients_tenant(ctx) -> None:
    api = ctx.api(ctx.users["manager"])
    response = api.post(f"/api/v1/campaigns/{ctx.a_campaign.pk}/clone/", {}, format="json")
    assert response.status_code == 201
    copy = Campaign.objects.get(pk=response.json()["id"])
    assert copy.client_id == ctx.a_client.pk
    assert copy.current_profile.client_id == ctx.a_client.pk
    assert copy.pk != ctx.a_campaign.pk
