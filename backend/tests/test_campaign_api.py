"""Campaign and CampaignProfile API (issue #48): CRUD, versioning, validation, roles, clone."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.campaigns import services
from apps.campaigns.memberships import grant_membership
from apps.campaigns.models import Campaign, CampaignProfile, Client
from apps.campaigns.reference import ISO_3166_ALPHA2
from apps.core.models import AuditAction, AuditLog, Job, JobStatus
from tests.factories import make_campaign, make_client, make_user

pytestmark = [pytest.mark.api, pytest.mark.django_db]

URL = "/api/v1/campaigns/"
ROLES = ["viewer", "reviewer", "manager", "admin", "global"]


def as_user(user: User) -> APIClient:
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return api


def user_with(role: str, *clients: Client) -> User:
    if role == "global":
        return make_user(is_superuser=True)
    user = make_user()
    for client in clients:
        grant_membership(client, user, role)
    return user


def api_for(role: str, client: Client) -> APIClient:
    return as_user(user_with(role, client))


def profile_payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "offer": "Managed security",
        "countries": ["sa", "AE"],
        "industries": ["Logistics"],
        "company_size_min": 10,
        "company_size_max": 200,
        "business_model": "b2b",
        "excluded_industries": ["Government"],
        "excluded_company_types": ["non-profit"],
        "target_departments": ["IT"],
        "preferred_buyer_titles": ["CIO", "Head of IT"],
        "outreach_languages": ["en", "AR"],
        "custom_rules": "Prefer own warehouses.",
        "change_note": "first",
    }
    body.update(overrides)
    return body


def create_body(client: Client, **profile: Any) -> dict[str, Any]:
    return {"client": str(client.pk), "name": "Q4 push", "profile": profile_payload(**profile)}


def details(response: Any) -> dict[str, Any]:
    assert response.status_code == 400, response.content
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    return dict(body["details"])


# ------------------------------------------------------------------ create / read


def test_create_and_read_all_brief_fields() -> None:
    client = make_client()
    api = api_for("manager", client)
    response = api.post(URL, create_body(client), format="json")
    assert response.status_code == 201, response.content
    assert response["X-Profile-Version-Created"] == "true"
    body = response.json()
    assert body["status"] == "draft"
    assert body["client"] == str(client.pk)
    assert body["profile_version"] == 1
    profile = body["profile"]
    assert profile["countries"] == ["SA", "AE"]  # normalised, order kept
    assert profile["outreach_languages"] == ["en", "ar"]
    assert profile["preferred_buyer_titles"] == ["CIO", "Head of IT"]
    assert profile["company_size_min"] == 10
    assert profile["business_model"] == "b2b"
    assert profile["change_note"] == "first"
    got = api.get(f"{URL}{body['id']}/")
    assert got.status_code == 200
    assert got.json() == body


def test_create_minimal_defaults() -> None:
    client = make_client()
    api = api_for("manager", client)
    response = api.post(
        URL,
        {"client": str(client.pk), "name": "Min", "profile": {"offer": "Thing"}},
        format="json",
    )
    assert response.status_code == 201
    profile = response.json()["profile"]
    assert profile["countries"] == []
    assert profile["business_model"] == "b2b"
    assert profile["company_size_min"] is None


def test_buyer_titles_dedupe_keeping_order() -> None:
    client = make_client()
    api = api_for("manager", client)
    response = api.post(
        URL,
        create_body(client, preferred_buyer_titles=["CFO", "CIO", "cfo", "CTO", "CIO"]),
        format="json",
    )
    assert response.json()["profile"]["preferred_buyer_titles"] == ["CFO", "CIO", "CTO"]


def test_client_and_profile_required_on_create() -> None:
    client = make_client()
    api = api_for("manager", client)
    errors = details(api.post(URL, {"name": "x"}, format="json"))
    assert set(errors) == {"client", "profile"}
    errors = details(api.post(URL, {"client": str(client.pk), "profile": {}, "name": "x"}, "json"))
    assert errors == {"profile": {"offer": ["This field is required."]}}


def test_duplicate_name_is_a_field_error() -> None:
    client = make_client()
    make_campaign(client=client, name="Taken")
    api = api_for("manager", client)
    body = create_body(client)
    body["name"] = "  taken "
    assert "name" in details(api.post(URL, body, format="json"))


@pytest.mark.parametrize("name", ["", "   ", None, 5, "x" * 201])
def test_invalid_names(name: Any) -> None:
    client = make_client()
    body = create_body(client)
    body["name"] = name
    assert "name" in details(api_for("manager", client).post(URL, body, format="json"))


def test_read_only_fields_are_rejected_not_ignored() -> None:
    client = make_client()
    body = create_body(client)
    body["status"] = "active"
    assert "status" in details(api_for("manager", client).post(URL, body, format="json"))


# ------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("countries", ["ZZ"]),
        ("countries", ["SAU"]),
        ("countries", [""]),
        ("countries", "SA"),
        ("countries", [1]),
        ("countries", [None]),
        ("outreach_languages", ["fr"]),
        ("outreach_languages", ["english"]),
        ("outreach_languages", "en"),
        ("business_model", "other"),
        ("company_size_min", -1),
        ("company_size_min", "many"),
        ("company_size_max", 1.5),
        ("offer", ""),
        ("offer", "  "),
        ("offer", None),
        ("industries", "retail"),
        ("industries", [["nested"]]),
        ("industries", ["x" * 201]),
        ("preferred_buyer_titles", [True]),
        ("custom_rules", 7),
        ("change_note", ["a"]),
    ],
)
def test_field_level_errors_nested_under_profile(field: str, value: Any) -> None:
    client = make_client()
    api = api_for("manager", client)
    errors = details(api.post(URL, create_body(client, **{field: value}), format="json"))
    assert list(errors) == ["profile"]
    assert field in errors["profile"], errors


def test_country_and_language_messages_name_the_bad_values() -> None:
    client = make_client()
    api = api_for("manager", client)
    response = api.post(
        URL,
        create_body(client, countries=["SA", "ZZ", "zz"], outreach_languages=["fr", "en"]),
        format="json",
    )
    errors = details(response)["profile"]
    assert "ZZ" in errors["countries"][0]
    assert errors["countries"][0].count("ZZ") == 1
    assert "fr" in errors["outreach_languages"][0]
    assert "en, ar" in errors["outreach_languages"][0]


def test_size_min_must_not_exceed_max() -> None:
    client = make_client()
    api = api_for("manager", client)
    errors = details(
        api.post(URL, create_body(client, company_size_min=500, company_size_max=10), format="json")
    )
    assert "company_size_max" in errors["profile"]
    ok = api.post(URL, create_body(client, company_size_min=10, company_size_max=10), format="json")
    assert ok.status_code == 201
    open_ended = api.post(
        URL,
        {**create_body(client), "name": "Open"}
        | {"profile": profile_payload(company_size_min=None, company_size_max=None)},
        format="json",
    )
    assert open_ended.status_code == 201


def test_size_check_uses_current_values_on_patch() -> None:
    campaign = make_campaign()  # 50..500
    api = api_for("manager", campaign.client)
    errors = details(
        api.patch(f"{URL}{campaign.pk}/", {"profile": {"company_size_max": 10}}, format="json")
    )
    assert "company_size_max" in errors["profile"]
    assert campaign.profiles.count() == 1


@override_settings(OUTREACH_LANGUAGES=["en", "ar", "fr"])
def test_supported_languages_come_from_a_setting() -> None:
    client = make_client()
    response = api_for("manager", client).post(
        URL, create_body(client, outreach_languages=["fr"]), format="json"
    )
    assert response.status_code == 201


def test_country_list_is_complete_and_well_formed() -> None:
    assert len(ISO_3166_ALPHA2) == 249
    assert all(len(c) == 2 and c.isupper() for c in ISO_3166_ALPHA2)
    assert {"SA", "AE", "US", "GB", "EG", "XK"} - ISO_3166_ALPHA2 == {"XK"}  # XK is not ISO


@pytest.mark.parametrize("key", ["structured_rules", "rules"])
def test_structured_rules_are_rejected_not_dropped(key: str) -> None:
    client = make_client()
    api = api_for("manager", client)
    inside = create_body(client)
    inside["profile"][key] = [{"if": "x"}]
    errors = details(api.post(URL, inside, format="json"))
    assert "not supported yet" in errors["profile"][key][0]
    outside = create_body(client)
    outside[key] = []
    assert "not supported yet" in details(api.post(URL, outside, format="json"))[key][0]
    assert not Campaign.objects.filter(client=client).exists()


def test_unknown_fields_are_rejected_with_all_errors_reported() -> None:
    campaign = make_campaign()
    api = api_for("manager", campaign.client)
    errors = details(
        api.patch(
            f"{URL}{campaign.pk}/",
            {"profile": {"bogus": 1, "countries": ["ZZ"]}, "offer": "top level"},
            format="json",
        )
    )
    assert errors["profile"]["bogus"] == ["Unknown field."]
    assert "countries" in errors["profile"]
    assert "Rules go inside" in errors["offer"][0]


def test_non_object_bodies_are_400() -> None:
    client = make_client()
    api = api_for("manager", client)
    assert api.post(URL, [1], format="json").status_code == 400
    assert api.post(URL, {**create_body(client), "profile": "x"}, format="json").status_code == 400


# ------------------------------------------------------------------ versioning


def versions(campaign: Campaign) -> list[int]:
    return list(campaign.profiles.order_by("version").values_list("version", flat=True))


def test_patch_rules_creates_new_version_and_keeps_old_retrievable() -> None:
    campaign = make_campaign()
    api = api_for("manager", campaign.client)
    old = api.get(f"{URL}{campaign.pk}/").json()["profile"]

    response = api.patch(
        f"{URL}{campaign.pk}/",
        {"profile": {"offer": "New offer", "countries": ["EG"], "change_note": "pivot"}},
        format="json",
    )
    assert response.status_code == 200
    assert response["X-Profile-Version-Created"] == "true"
    body = response.json()
    assert body["profile_version"] == 2
    assert body["profile"]["offer"] == "New offer"
    assert body["profile"]["countries"] == ["EG"]
    assert body["profile"]["industries"] == old["industries"]  # carried over
    assert body["profile"]["change_note"] == "pivot"
    assert versions(campaign) == [1, 2]

    history = api.get(f"{URL}{campaign.pk}/profile-versions/").json()
    assert [v["version"] for v in history["results"]] == [2, 1]  # newest first
    assert history["count"] == 2
    v1 = api.get(f"{URL}{campaign.pk}/profile-versions/1/")
    assert v1.status_code == 200
    assert v1.json() == old  # unchanged, byte for byte
    assert api.get(f"{URL}{campaign.pk}/profile-versions/2/").json() == body["profile"]
    assert api.get(f"{URL}{campaign.pk}/profile-versions/3/").status_code == 404
    assert api.get(f"{URL}{campaign.pk}/profile-versions/0/").status_code == 404
    assert api.get(f"{URL}{campaign.pk}/profile-versions/99999999999999999999/").status_code == 404
    assert CampaignProfile.objects.get(campaign=campaign, version=1).offer == old["offer"]


def test_unchanged_payload_returns_current_version_without_bump() -> None:
    campaign = make_campaign()
    api = api_for("manager", campaign.client)
    current = api.get(f"{URL}{campaign.pk}/").json()
    same = {
        k: current["profile"][k]
        for k in profile_payload()
        if k in current["profile"] and k != "change_note"
    }
    for method in ("patch", "put"):
        body = {"name": campaign.name, "profile": same}
        response = getattr(api, method)(f"{URL}{campaign.pk}/", body, format="json")
        assert response.status_code == 200, response.content
        assert response["X-Profile-Version-Created"] == "false"
        assert response.json()["profile_version"] == 1
    assert versions(campaign) == [1]
    assert not AuditLog.objects.filter(object_type="campaign_profile").exists()
    assert not AuditLog.objects.filter(object_type="campaign", action="update").exists()


def test_reordering_titles_is_a_change() -> None:
    campaign = make_campaign()  # CIO, Head of IT, IT Manager
    api = api_for("manager", campaign.client)
    response = api.patch(
        f"{URL}{campaign.pk}/",
        {"profile": {"preferred_buyer_titles": ["IT Manager", "CIO", "Head of IT"]}},
        format="json",
    )
    assert response.json()["profile_version"] == 2
    assert response.json()["profile"]["preferred_buyer_titles"][0] == "IT Manager"


def test_put_replaces_name_and_rules_in_one_request() -> None:
    campaign = make_campaign(name="Old")
    api = api_for("manager", campaign.client)
    response = api.put(
        f"{URL}{campaign.pk}/",
        {"name": "Renamed", "profile": {"offer": "Different"}},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"
    assert response.json()["profile_version"] == 2
    assert response.json()["profile"]["countries"] == ["SA", "AE"]  # omitted: carried over


def test_put_requires_name_and_profile() -> None:
    campaign = make_campaign()
    api = api_for("manager", campaign.client)
    assert set(details(api.put(f"{URL}{campaign.pk}/", {}, format="json"))) == {"name"}
    assert set(details(api.put(f"{URL}{campaign.pk}/", {"name": "x"}, format="json"))) == {
        "profile"
    }
    errors = details(api.put(f"{URL}{campaign.pk}/", {"name": "x", "profile": {}}, format="json"))
    assert errors == {"profile": {"offer": ["This field is required."]}}


def test_name_only_patch_goes_through_update_campaign_without_new_version() -> None:
    campaign = make_campaign(name="Before")
    actor = user_with("manager", campaign.client)
    response = as_user(actor).patch(f"{URL}{campaign.pk}/", {"name": "After"}, format="json")
    assert response.status_code == 200
    assert response["X-Profile-Version-Created"] == "false"
    assert response.json()["profile_version"] == 1
    (row,) = AuditLog.objects.filter(object_type="campaign", action=AuditAction.UPDATE)
    assert row.actor_id == actor.pk
    assert row.before == {"name": "Before"}
    assert row.after == {"name": "After"}


def test_clearing_a_rule_is_possible() -> None:
    campaign = make_campaign()
    api = api_for("manager", campaign.client)
    response = api.patch(
        f"{URL}{campaign.pk}/",
        {"profile": {"excluded_industries": [], "company_size_min": None, "custom_rules": ""}},
        format="json",
    )
    profile = response.json()["profile"]
    assert response.json()["profile_version"] == 2
    assert profile["excluded_industries"] == []
    assert profile["company_size_min"] is None
    assert profile["custom_rules"] == ""


def test_rule_edit_is_audited_with_actor_and_changed_fields() -> None:
    campaign = make_campaign()
    actor = user_with("manager", campaign.client)
    as_user(actor).patch(
        f"{URL}{campaign.pk}/", {"profile": {"offer": "Changed", "change_note": "why"}}, "json"
    )
    (row,) = AuditLog.objects.filter(object_type="campaign_profile")
    assert row.actor_id == actor.pk
    assert row.client_id == campaign.client_id
    assert row.after is not None
    assert row.after["version"] == 2
    assert row.after["changed_fields"] == ["offer"]
    assert row.after["change_note"] == "why"


def test_failed_edit_rolls_back_the_rename() -> None:
    campaign = make_campaign(name="Keep")
    api = api_for("manager", campaign.client)
    response = api.patch(
        f"{URL}{campaign.pk}/",
        {"name": "Renamed", "profile": {"countries": ["ZZ"]}},
        format="json",
    )
    assert response.status_code == 400
    campaign.refresh_from_db()
    assert campaign.name == "Keep"
    assert not AuditLog.objects.filter(object_type="campaign", action="update").exists()


def test_concurrent_edits_get_consecutive_versions() -> None:
    campaign = make_campaign()
    api = api_for("manager", campaign.client)
    for n in range(3):
        api.patch(f"{URL}{campaign.pk}/", {"profile": {"offer": f"Offer {n}"}}, format="json")
    assert versions(campaign) == [1, 2, 3, 4]


def test_client_cannot_be_changed() -> None:
    campaign = make_campaign()
    other = make_client()
    api = as_user(user_with("manager", campaign.client, other))
    assert "client" in details(
        api.patch(f"{URL}{campaign.pk}/", {"client": str(other.pk)}, format="json")
    )
    same = api.patch(f"{URL}{campaign.pk}/", {"client": str(campaign.client_id)}, format="json")
    assert same.status_code == 200


# ------------------------------------------------------------------ rules summary


def test_rules_summary_shape_snapshot() -> None:
    campaign = make_campaign(name="Snapshot")
    api = api_for("viewer", campaign.client)
    response = api.get(f"{URL}{campaign.pk}/rules-summary/")
    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "campaign": {"id": str(campaign.pk), "name": "Snapshot"},
        "profile_version": 1,
        "offer": "Managed cloud security for mid-size companies",
        "targeting": {
            "countries": ["SA", "AE"],
            "industries": ["Logistics", "Retail"],
            "company_size": {"min": 50, "max": 500},
            "business_model": "b2b",
        },
        "exclusions": {"industries": ["Government"], "company_types": ["non-profit"]},
        "buyers": {
            "target_departments": ["IT", "Operations"],
            "preferred_buyer_titles": ["CIO", "Head of IT", "IT Manager"],
        },
        "outreach": {"languages": ["en", "ar"]},
        "custom_rules": "Prefer companies with their own warehouses.",
        "structured_rules": [],
    }
    # Key order is part of what prompts see.
    assert list(response.json()) == [
        "schema_version",
        "campaign",
        "profile_version",
        "offer",
        "targeting",
        "exclusions",
        "buyers",
        "outreach",
        "custom_rules",
        "structured_rules",
    ]


def test_rules_summary_follows_versions() -> None:
    campaign = make_campaign()
    api = api_for("manager", campaign.client)
    api.patch(f"{URL}{campaign.pk}/", {"profile": {"offer": "v2 offer"}}, format="json")
    url = f"{URL}{campaign.pk}/rules-summary/"
    assert api.get(url).json()["profile_version"] == 2
    assert api.get(url).json()["offer"] == "v2 offer"
    old = api.get(url, {"version": 1}).json()
    assert old["profile_version"] == 1
    assert old["offer"].startswith("Managed cloud")
    assert api.get(url, {"version": 9}).status_code == 404
    assert api.get(url, {"version": 0}).status_code == 400
    assert api.get(url, {"version": "x"}).status_code == 400


def test_rules_summary_is_read_only() -> None:
    campaign = make_campaign()
    api = api_for("manager", campaign.client)
    assert api.post(f"{URL}{campaign.pk}/rules-summary/", {}, format="json").status_code == 405
    assert api.put(f"{URL}{campaign.pk}/rules-summary/", {}, format="json").status_code == 405


# ------------------------------------------------------------------ list / filters / nested


def test_list_filters_search_ordering() -> None:
    a, b = make_client(name="A"), make_client(name="B")
    one = make_campaign(client=a, name="Alpha")
    two = make_campaign(client=a, name="Beta")
    three = make_campaign(client=b, name="Gamma")
    api = as_user(user_with("global"))

    def names(**params: Any) -> list[str]:
        return [r["name"] for r in api.get(URL, params).json()["results"]]

    assert names() == ["Alpha", "Beta", "Gamma"]
    assert names(client=str(a.pk)) == ["Alpha", "Beta"]
    assert names(search="gam") == ["Gamma"]
    assert names(ordering="-name") == ["Gamma", "Beta", "Alpha"]
    services.activate_campaign(two)
    assert names(status="active") == ["Beta"]
    services.archive_campaign(three)
    assert names() == ["Alpha", "Beta"]
    assert names(archived="true") == ["Gamma"]
    assert names(status="archived") == ["Gamma"]
    assert names(archived="all") == ["Alpha", "Beta", "Gamma"]
    assert api.get(URL, {"status": "bogus"}).status_code == 400
    assert api.get(URL, {"archived": "maybe"}).status_code == 400
    assert api.get(URL, {"client": "not-a-uuid"}).status_code == 400
    assert one.pk


def test_nested_list_and_create_under_a_client() -> None:
    client, other = make_client(), make_client()
    make_campaign(client=client, name="Mine")
    make_campaign(client=other, name="Theirs")
    user = user_with("manager", client)
    api = as_user(user)
    listed = api.get(f"/api/v1/clients/{client.pk}/campaigns/").json()
    assert [r["name"] for r in listed["results"]] == ["Mine"]

    body = {"name": "Nested", "profile": profile_payload()}
    created = api.post(f"/api/v1/clients/{client.pk}/campaigns/", body, format="json")
    assert created.status_code == 201, created.content
    assert created.json()["client"] == str(client.pk)
    echoed = api.post(
        f"/api/v1/clients/{client.pk}/campaigns/",
        {**body, "name": "Echo", "client": str(client.pk)},
        format="json",
    )
    assert echoed.status_code == 201
    mismatch = api.post(
        f"/api/v1/clients/{client.pk}/campaigns/",
        {**body, "name": "Bad", "client": str(other.pk)},
        format="json",
    )
    assert "client" in details(mismatch)


def test_nested_route_hides_foreign_and_unknown_clients() -> None:
    mine, theirs = make_client(), make_client()
    api = as_user(user_with("admin", mine))
    for client_id in (theirs.pk, uuid.uuid4()):
        assert api.get(f"/api/v1/clients/{client_id}/campaigns/").status_code == 404
        body = {"name": "x", "profile": profile_payload()}
        assert api.post(f"/api/v1/clients/{client_id}/campaigns/", body, "json").status_code == 404
    assert not Campaign.objects.filter(client=theirs).exists()


def test_nested_route_has_no_detail_or_other_methods() -> None:
    client = make_client()
    api = api_for("admin", client)
    assert api.delete(f"/api/v1/clients/{client.pk}/campaigns/").status_code == 405
    assert api.put(f"/api/v1/clients/{client.pk}/campaigns/", {}, format="json").status_code == 405


def test_nested_list_of_archived_client_still_works() -> None:
    campaign = make_campaign()
    root = as_user(user_with("global"))
    services.archive_client(campaign.client)
    assert root.get(f"/api/v1/clients/{campaign.client_id}/campaigns/").json()["count"] == 1


def test_create_under_archived_client_is_400() -> None:
    client = make_client()
    services.archive_client(client)
    response = as_user(user_with("global")).post(URL, create_body(client), format="json")
    assert "archived client" in response.json()["error"]["details"]["non_field_errors"][0]


# ------------------------------------------------------------------ archive / restore / activate


def test_archive_restore_cycle_keeps_history() -> None:
    campaign = make_campaign(name="Cycle")
    manager = api_for("manager", campaign.client)
    manager.patch(f"{URL}{campaign.pk}/", {"profile": {"offer": "v2"}}, format="json")
    admin = api_for("admin", campaign.client)

    archived = admin.post(f"{URL}{campaign.pk}/archive/")
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert archived.json()["archived_at"] is not None
    assert admin.post(f"{URL}{campaign.pk}/archive/").status_code == 200  # idempotent
    assert admin.get(f"{URL}{campaign.pk}/").status_code == 200  # still retrievable
    assert admin.get(f"{URL}{campaign.pk}/profile-versions/").json()["count"] == 2
    assert admin.get(f"{URL}{campaign.pk}/rules-summary/").status_code == 200
    assert admin.get(URL).json()["count"] == 0

    restored = admin.post(f"{URL}{campaign.pk}/restore/")
    assert restored.status_code == 200
    assert (restored.json()["status"], restored.json()["archived_at"]) == ("draft", None)
    assert [r["action"] for r in AuditLog.objects.order_by("created_at").values("action")][-2:] == [
        AuditAction.ARCHIVE,
        AuditAction.RESTORE,
    ]


def test_archived_campaign_is_read_only() -> None:
    campaign = make_campaign()
    services.archive_campaign(campaign)
    api = api_for("manager", campaign.client)
    for method, body in (
        ("patch", {"name": "x"}),
        ("patch", {}),
        ("patch", {"profile": {"offer": "x"}}),
        ("put", {"name": "x", "profile": {"offer": "x"}}),
    ):
        response = getattr(api, method)(f"{URL}{campaign.pk}/", body, format="json")
        assert response.status_code == 400, (method, body)
        assert "read-only" in response.json()["error"]["details"]["non_field_errors"][0]
    assert api.post(f"{URL}{campaign.pk}/activate/").status_code == 400
    assert versions(campaign) == [1]


def test_restore_name_conflict_is_a_field_error() -> None:
    campaign = make_campaign(name="Same")
    services.archive_campaign(campaign)
    make_campaign(client=campaign.client, name="same")
    response = api_for("admin", campaign.client).post(f"{URL}{campaign.pk}/restore/")
    assert "name" in details(response)


def test_restore_under_archived_client_is_400() -> None:
    campaign = make_campaign()
    services.archive_campaign(campaign)
    services.archive_client(campaign.client)
    response = as_user(user_with("global")).post(f"{URL}{campaign.pk}/restore/")
    assert "Restore the client" in response.json()["error"]["details"]["non_field_errors"][0]


def test_archive_with_active_jobs_is_a_409_envelope() -> None:
    campaign = make_campaign()
    Job.objects.create(
        client=campaign.client, campaign=campaign, type="demo", status=JobStatus.RUNNING
    )
    api = api_for("admin", campaign.client)
    response = api.post(f"{URL}{campaign.pk}/archive/")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "campaign_has_active_jobs"
    campaign.refresh_from_db()
    assert not campaign.is_archived

    Job.objects.filter(campaign=campaign).update(
        status=JobStatus.SUCCEEDED, finished_at=timezone.now()
    )
    assert api.post(f"{URL}{campaign.pk}/archive/").status_code == 200


def test_activate() -> None:
    campaign = make_campaign()
    api = api_for("manager", campaign.client)
    response = api.post(f"{URL}{campaign.pk}/activate/")
    assert response.status_code == 200
    assert response.json()["status"] == "active"
    assert response.json()["profile_version"] == 1


def test_no_delete_method() -> None:
    campaign = make_campaign()
    assert api_for("admin", campaign.client).delete(f"{URL}{campaign.pk}/").status_code == 405
    assert Campaign.objects.filter(pk=campaign.pk).exists()


# ------------------------------------------------------------------ clone


def test_clone_is_independent_with_a_copy_of_the_current_profile() -> None:
    source = make_campaign(name="Source")
    manager = user_with("manager", source.client)
    api = as_user(manager)
    api.patch(f"{URL}{source.pk}/", {"profile": {"offer": "Latest offer"}}, format="json")
    api.post(f"{URL}{source.pk}/activate/")
    source.refresh_from_db()

    response = api.post(f"{URL}{source.pk}/clone/", {}, format="json")
    assert response.status_code == 201, response.content
    copy = response.json()
    assert copy["id"] != str(source.pk)
    assert copy["name"] == "Source (copy)"
    assert copy["status"] == "draft"  # not the source's active
    assert copy["client"] == str(source.client_id)
    assert copy["profile_version"] == 1  # history is not copied
    assert copy["profile"]["offer"] == "Latest offer"  # the CURRENT rules
    assert copy["profile"]["id"] != str(source.current_profile_id)
    assert str(source.pk) in copy["profile"]["change_note"]
    clone_row = Campaign.objects.get(pk=copy["id"])
    assert clone_row.created_by == manager

    # Editing either one never touches the other.
    api.patch(f"{URL}{copy['id']}/", {"profile": {"offer": "Copy only"}, "name": "Fork"}, "json")
    source.refresh_from_db()
    assert source.name == "Source"
    assert source.current_profile.offer == "Latest offer"
    assert versions(source) == [1, 2]
    api.patch(f"{URL}{source.pk}/", {"profile": {"offer": "Source only"}}, format="json")
    clone_row.refresh_from_db()
    assert clone_row.current_profile.offer == "Copy only"
    assert versions(clone_row) == [1, 2]
    assert set(source.profiles.values_list("id", flat=True)).isdisjoint(
        clone_row.profiles.values_list("id", flat=True)
    )


def test_clone_is_audited_with_its_source() -> None:
    source = make_campaign()
    actor = user_with("manager", source.client)
    copy_id = as_user(actor).post(f"{URL}{source.pk}/clone/", {}, format="json").json()["id"]
    (row,) = AuditLog.objects.filter(object_id=copy_id, action=AuditAction.CREATE)
    assert row.object_type == "campaign"
    assert row.actor_id == actor.pk
    assert row.client_id == source.client_id
    assert row.after is not None
    assert row.after["cloned_from"] == str(source.pk)
    assert row.after["cloned_from_version"] == 1
    assert row.after["profile_version"] == 1


def test_clone_names() -> None:
    source = make_campaign(name="Base")
    api = api_for("manager", source.client)
    first = api.post(f"{URL}{source.pk}/clone/", {}, format="json").json()["name"]
    second = api.post(f"{URL}{source.pk}/clone/", {}, format="json").json()["name"]
    assert (first, second) == ("Base (copy)", "Base (copy 2)")
    custom = api.post(f"{URL}{source.pk}/clone/", {"name": "Chosen"}, format="json")
    assert custom.json()["name"] == "Chosen"
    taken = api.post(f"{URL}{source.pk}/clone/", {"name": "chosen"}, format="json")
    assert "name" in details(taken)
    assert "name" in details(api.post(f"{URL}{source.pk}/clone/", {"name": ""}, format="json"))


def test_clone_long_name_is_truncated_to_fit() -> None:
    source = make_campaign(name="N" * 200)
    response = api_for("manager", source.client).post(f"{URL}{source.pk}/clone/")
    assert response.status_code == 201
    assert len(response.json()["name"]) == 200
    assert response.json()["name"].endswith(" (copy)")


def test_clone_of_archived_source_works_but_not_under_archived_client() -> None:
    source = make_campaign()
    services.archive_campaign(source)
    root = as_user(user_with("global"))
    assert root.post(f"{URL}{source.pk}/clone/").status_code == 201
    services.archive_client(source.client)
    assert root.post(f"{URL}{source.pk}/clone/").status_code == 400


# ------------------------------------------------------------------ roles x actions

# action -> (method, path template, body factory)
ACTIONS: dict[str, tuple[str, str, dict[str, Any]]] = {
    "list": ("get", "", {}),
    "retrieve": ("get", "{id}/", {}),
    "profile_versions": ("get", "{id}/profile-versions/", {}),
    "profile_version": ("get", "{id}/profile-versions/1/", {}),
    "rules_summary": ("get", "{id}/rules-summary/", {}),
    "create": ("post", "", {}),
    "rename": ("patch", "{id}/", {"name": "Renamed"}),
    "edit_rules": ("patch", "{id}/", {"profile": {"offer": "Edited by role"}}),
    "put": ("put", "{id}/", {"name": "Put", "profile": {"offer": "Put offer"}}),
    "clone": ("post", "{id}/clone/", {}),
    "activate": ("post", "{id}/activate/", {}),
    "archive": ("post", "{id}/archive/", {}),
    "restore": ("post", "{id}/restore/", {}),
}
READERS = {"viewer", "reviewer", "manager", "admin", "global"}
EDITORS = {"manager", "admin", "global"}
ALLOWED = {
    "list": READERS,
    "retrieve": READERS,
    "profile_versions": READERS,
    "profile_version": READERS,
    "rules_summary": READERS,
    "create": EDITORS,
    "rename": EDITORS,
    "edit_rules": EDITORS,
    "put": EDITORS,
    "clone": EDITORS,
    "activate": EDITORS,
    "archive": {"admin", "global"},
    "restore": {"admin", "global"},
}


def call(api: APIClient, action: str, campaign: Campaign) -> int:
    method, suffix, body = ACTIONS[action]
    if action == "create":
        body = create_body(campaign.client)
        body["name"] = "Fresh by role"
    kwargs: dict[str, Any] = {"format": "json"} if body else {}
    path = URL + suffix.format(id=campaign.pk)
    return int(getattr(api, method)(path, body or None, **kwargs).status_code)


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("action", list(ACTIONS))
def test_role_matrix(role: str, action: str) -> None:
    campaign = make_campaign()
    if action == "restore":
        services.archive_campaign(campaign)
    status = call(api_for(role, campaign.client), action, campaign)
    if role in ALLOWED[action]:
        assert status in (200, 201), (role, action, status)
    else:
        assert status == 403, (role, action, status)


def test_reviewers_cannot_edit_rules_and_nothing_changes() -> None:
    campaign = make_campaign()
    api = api_for("reviewer", campaign.client)
    for method, body in (
        ("patch", {"profile": {"offer": "Sneaky"}}),
        ("put", {"name": campaign.name, "profile": {"offer": "Sneaky"}}),
    ):
        response = getattr(api, method)(f"{URL}{campaign.pk}/", body, format="json")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "permission_denied"
    assert versions(campaign) == [1]
    assert not AuditLog.objects.filter(object_type="campaign_profile").exists()


@pytest.mark.parametrize("role", ["viewer", "reviewer", "manager", "admin"])
@pytest.mark.parametrize("action", list(ACTIONS))
def test_other_clients_campaign_is_404_for_every_action(role: str, action: str) -> None:
    mine, theirs = make_client(), make_client()
    campaign = make_campaign(client=theirs)
    if action == "restore":
        services.archive_campaign(campaign)
    user = user_with(role, mine)
    status = call(as_user(user), action, campaign)
    expected = 404
    if action == "list":
        expected = 200  # empty, nothing leaks (see test_list_only_contains_own_clients_campaigns)
    elif action == "create" and role in ("viewer", "reviewer"):
        expected = 403  # no EDIT role anywhere: refused before the target client is looked at
    assert status == expected, (role, action, status)
    assert Campaign.objects.filter(client=theirs).count() == 1
    assert versions(campaign) == [1]


def test_list_only_contains_own_clients_campaigns() -> None:
    mine, theirs = make_client(), make_client()
    make_campaign(client=mine, name="Mine")
    make_campaign(client=theirs, name="Theirs")
    body = as_user(user_with("viewer", mine)).get(URL).json()
    assert [r["name"] for r in body["results"]] == ["Mine"]
    leak = as_user(user_with("viewer", mine)).get(URL, {"client": str(theirs.pk)}).json()
    assert leak["results"] == []


def test_create_in_a_client_you_only_view_is_403_and_foreign_is_404() -> None:
    viewed, foreign = make_client(), make_client()
    user = make_user()
    grant_membership(viewed, user, "viewer")
    grant_membership(make_client(), user, "manager")  # has EDIT somewhere
    api = as_user(user)
    assert api.post(URL, create_body(viewed), format="json").status_code == 403
    assert api.post(URL, create_body(foreign), format="json").status_code == 404
    assert (
        api.post(URL, {**create_body(viewed), "client": "nope"}, format="json").status_code == 400
    )
    missing = {**create_body(viewed), "client": str(uuid.uuid4())}
    assert api.post(URL, missing, format="json").status_code == 404


def test_anonymous_and_inactive_users() -> None:
    campaign = make_campaign()
    assert APIClient().get(URL).status_code == 401
    assert APIClient().get(f"{URL}{campaign.pk}/").status_code == 401
    user = user_with("admin", campaign.client)
    api = as_user(user)
    User.objects.filter(pk=user.pk).update(is_active=False)
    assert api.get(URL).status_code in (401, 403)


def test_revoked_membership_loses_access_on_next_request() -> None:
    campaign = make_campaign()
    user = user_with("manager", campaign.client)
    api = as_user(user)
    assert api.get(f"{URL}{campaign.pk}/").status_code == 200
    from apps.campaigns.memberships import revoke_membership

    revoke_membership(campaign.client, user)
    assert api.get(f"{URL}{campaign.pk}/").status_code == 404
    assert api.patch(f"{URL}{campaign.pk}/", {"name": "x"}, format="json").status_code == 404


def test_malformed_ids_are_404() -> None:
    api = as_user(user_with("global"))
    assert api.get(f"{URL}not-a-uuid/").status_code == 404
    assert api.get(f"{URL}{uuid.uuid4()}/").status_code == 404


# ------------------------------------------------------------------ queries


def test_list_query_count_does_not_grow_with_campaigns() -> None:
    client = make_client()
    user = user_with("viewer", client)
    make_campaign(client=client)
    api = as_user(user)
    api.get(URL)  # warm up
    with CaptureQueriesContext(connection) as small:
        assert api.get(URL).json()["count"] == 1
    for _ in range(15):
        make_campaign(client=client)
    with CaptureQueriesContext(connection) as large:
        assert api.get(URL).json()["count"] == 16
    assert len(large) == len(small)
    assert len(large) <= 5

    with CaptureQueriesContext(connection) as nested:
        assert api.get(f"/api/v1/clients/{client.pk}/campaigns/").json()["count"] == 16
    assert len(nested) <= 8  # + client lookup (404 check)


def test_history_and_detail_query_counts_are_flat() -> None:
    campaign = make_campaign()
    api = api_for("manager", campaign.client)
    for n in range(10):
        api.patch(f"{URL}{campaign.pk}/", {"profile": {"offer": f"o{n}"}}, format="json")
    with CaptureQueriesContext(connection) as history:
        assert api.get(f"{URL}{campaign.pk}/profile-versions/").json()["count"] == 11
    with CaptureQueriesContext(connection) as detail:
        api.get(f"{URL}{campaign.pk}/")
    with CaptureQueriesContext(connection) as summary:
        api.get(f"{URL}{campaign.pk}/rules-summary/")
    assert len(history) <= 6
    assert len(detail) <= 5
    assert len(summary) <= 5


# ------------------------------------------------------------------ services


def test_service_clone_and_restore_rules() -> None:
    from django.core.exceptions import ValidationError

    source = make_campaign(name="Svc")
    copy = services.clone_campaign(source, name="Svc Two")
    assert copy.current_profile.offer == source.current_profile.offer
    assert copy.current_profile.version == 1
    with pytest.raises(ValidationError):
        services.clone_campaign(source, name="svc two")
    with pytest.raises(services.ProfileUnchangedError):
        services.create_profile_version(copy, {})
