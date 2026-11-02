"""Per-campaign trigger windows (#114): how long each kind of signal counts as a current trigger.

The default window of every signal type stays in code; a campaign may set its own. It is applied
when trigger status is read, so changing it takes effect on signals already stored and nothing is
rewritten (signals are append-only).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.campaigns import services as campaign_services
from apps.research import services
from apps.research.models import Signal
from apps.research.windows import (
    FRESH_DAYS,
    MAX_WINDOW_DAYS,
    expiry_for,
    usable_windows,
    validate_windows,
    window_days,
)
from tests.factories import make_campaign, make_client, make_company, make_signal
from tests.test_company_api import api_for

pytestmark = [pytest.mark.django_db]

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def signal(company: Any, kind: str, event: date, *, stored: bool = True) -> Signal:
    """A signal with the default expiry stored, or with none (``stored=False``)."""
    made = make_signal(
        company=company,
        type=kind,
        event_date=event,
        expires_at=expiry_for(kind, event) if stored else None,
    )
    return cast(Signal, made)


# ------------------------------------------------------------------ the windows themselves


def test_a_window_is_the_campaigns_or_the_default() -> None:
    assert window_days("funding") == FRESH_DAYS["funding"]
    assert window_days("funding", {"funding": 90}) == 90
    assert window_days("sales_hiring", {"funding": 90}) == FRESH_DAYS["sales_hiring"]
    assert expiry_for("funding", date(2026, 1, 1), {"funding": 90}) == datetime(
        2026, 4, 1, tzinfo=UTC
    )


def test_good_windows_are_accepted_and_empty_means_the_defaults() -> None:
    assert validate_windows({"funding": 90, "sales_hiring": 30}) == {
        "funding": 90,
        "sales_hiring": 30,
    }
    assert validate_windows({}) == {}
    assert validate_windows(None) == {}


@pytest.mark.parametrize(
    ("raw", "bad"),
    [
        ({"nonsense": 10}, "nonsense"),
        ({"funding": 0}, "funding"),
        ({"funding": -5}, "funding"),
        ({"funding": MAX_WINDOW_DAYS + 1}, "funding"),
        ({"funding": "90"}, "funding"),
        ({"funding": 12.5}, "funding"),
        ({"funding": True}, "funding"),
    ],
)
def test_bad_windows_are_refused_by_name(raw: dict[str, Any], bad: str) -> None:
    with pytest.raises(ValidationError) as info:
        validate_windows(raw)
    assert bad in info.value.message_dict


def test_windows_that_are_not_an_object_are_refused() -> None:
    with pytest.raises(ValidationError):
        validate_windows(["funding"])


def test_stored_junk_is_ignored_when_applying_windows() -> None:
    stored = {"funding": 90, "old_type": 5, "sales_hiring": "x", "bd_hiring": 0, "other": 20}
    assert usable_windows(stored) == {"funding": 90, "other": 20}
    assert usable_windows(None) == {}
    assert usable_windows("nope") == {}


# ------------------------------------------------------------------ freshness at a given time


def test_a_shorter_window_makes_an_older_signal_stale() -> None:
    company = make_company()
    funding = signal(company, "funding", date(2026, 1, 1))
    assert Signal.objects.fresh(NOW).filter(pk=funding.pk).exists()  # default: 365 days
    assert not Signal.objects.fresh(NOW, {"funding": 90}).filter(pk=funding.pk).exists()


def test_a_longer_window_keeps_a_signal_that_the_default_would_expire() -> None:
    company = make_company()
    funding = signal(company, "funding", date(2025, 6, 1))
    assert not Signal.objects.fresh(NOW).filter(pk=funding.pk).exists()
    assert Signal.objects.fresh(NOW, {"funding": 400}).filter(pk=funding.pk).exists()


def test_a_signal_goes_stale_at_the_instant_its_window_ends() -> None:
    company = make_company()
    event = signal(company, "funding", date(2026, 5, 2))  # 30 days: ends 2026-06-01 00:00 UTC
    windows = {"funding": 30}
    before = datetime(2026, 5, 31, 23, 59, tzinfo=UTC)
    at = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    assert Signal.objects.fresh(before, windows).filter(pk=event.pk).exists()
    assert not Signal.objects.fresh(at, windows).filter(pk=event.pk).exists()


def test_other_types_keep_their_stored_expiry() -> None:
    company = make_company()
    hiring = signal(company, "sales_hiring", date(2026, 5, 1))  # default 60 days
    old_hiring = signal(company, "sales_hiring", date(2026, 1, 1))
    fresh = Signal.objects.fresh(NOW, {"funding": 10})
    assert fresh.filter(pk=hiring.pk).exists()
    assert not fresh.filter(pk=old_hiring.pk).exists()


def test_a_signal_with_no_stored_expiry_follows_the_campaigns_window() -> None:
    company = make_company()
    undated_rule = signal(company, "funding", date(2025, 1, 1), stored=False)
    assert Signal.objects.fresh(NOW).filter(pk=undated_rule.pk).exists()  # no rule: fresh
    assert not Signal.objects.fresh(NOW, {"funding": 30}).filter(pk=undated_rule.pk).exists()


# ------------------------------------------------------------------ the campaign's windows


def test_the_trigger_of_a_company_follows_its_campaigns_windows() -> None:
    campaign = make_campaign()
    company = make_company(campaign=campaign)
    event = signal(company, "funding", date(2026, 1, 1))
    assert services.company_trigger_state(company, NOW).triggered is True
    campaign_services.set_trigger_windows(campaign, {"funding": 90})
    company.refresh_from_db()
    assert services.company_trigger_state(company, NOW).triggered is False
    campaign_services.set_trigger_windows(campaign, {})  # back to the defaults
    company.refresh_from_db()
    assert services.company_trigger_state(company, NOW).triggered is True
    assert Signal.objects.filter(pk=event.pk).exists()


def test_changing_a_window_rewrites_nothing_and_expired_signals_stay_queryable() -> None:
    campaign = make_campaign()
    company = make_company(campaign=campaign)
    event = signal(company, "funding", date(2026, 1, 1))
    stored_expiry = event.expires_at
    campaign_services.set_trigger_windows(campaign, {"funding": 30})
    event.refresh_from_db()
    assert event.expires_at == stored_expiry
    stale = Signal.objects.for_company(company).current()
    assert list(stale) == [event]
    assert not Signal.objects.for_company(company).fresh(NOW, {"funding": 30}).exists()


def test_windows_apply_per_campaign_not_across_campaigns() -> None:
    short = make_campaign(client=make_client())
    other = make_campaign(client=make_client())
    campaign_services.set_trigger_windows(short, {"funding": 30})
    first = make_company(campaign=short)
    second = make_company(campaign=other)
    for company in (first, second):
        signal(company, "funding", date(2026, 5, 1))
    found = services.fresh_signals_for([first, second], NOW)
    assert {s.company_id for s in found} == {second.pk}  # 30 days is over; 365 is not


def test_an_archived_campaign_keeps_its_windows_read_only() -> None:
    campaign = make_campaign()
    campaign_services.archive_campaign(campaign)
    with pytest.raises(ValidationError):
        campaign_services.set_trigger_windows(campaign, {"funding": 30})


# ------------------------------------------------------------------ the API


def url(campaign: Any) -> str:
    return f"/api/v1/campaigns/{campaign.pk}/trigger-windows/"


def test_the_endpoint_shows_the_defaults_and_the_campaigns_own_windows() -> None:
    client = make_client()
    campaign = make_campaign(client=client)
    api = api_for("viewer", client)
    body = api.get(url(campaign)).json()
    assert body["windows"] == {}
    assert body["defaults"]["funding"] == FRESH_DAYS["funding"]
    assert body["max_days"] == MAX_WINDOW_DAYS
    campaign_services.set_trigger_windows(campaign, {"funding": 120})
    assert api.get(url(campaign)).json()["windows"] == {"funding": 120}


def test_a_manager_sets_and_resets_the_windows() -> None:
    client = make_client()
    campaign = make_campaign(client=client)
    manager = api_for("manager", client)
    done = manager.put(
        url(campaign), {"windows": {"funding": 120, "sales_hiring": 30}}, format="json"
    )
    assert done.status_code == 200, done.content
    assert done.json()["windows"] == {"funding": 120, "sales_hiring": 30}
    campaign.refresh_from_db()
    assert campaign.trigger_windows == {"funding": 120, "sales_hiring": 30}
    reset = manager.put(url(campaign), {"windows": {}}, format="json")
    assert reset.json()["windows"] == {}


@pytest.mark.parametrize(
    "windows",
    [{"nonsense": 10}, {"funding": 0}, {"funding": MAX_WINDOW_DAYS + 1}, {"funding": "soon"}],
)
def test_bad_windows_are_a_400_that_names_the_type(windows: dict[str, Any]) -> None:
    client = make_client()
    campaign = make_campaign(client=client)
    response = api_for("manager", client).put(url(campaign), {"windows": windows}, format="json")
    assert response.status_code == 400
    assert next(iter(windows)) in response.content.decode()
    campaign.refresh_from_db()
    assert campaign.trigger_windows == {}


def test_a_viewer_can_read_but_not_change_the_windows() -> None:
    client = make_client()
    campaign = make_campaign(client=client)
    viewer = api_for("viewer", client)
    assert viewer.get(url(campaign)).status_code == 200
    assert viewer.put(url(campaign), {"windows": {"funding": 30}}, format="json").status_code == 403


def test_another_clients_campaign_is_not_found() -> None:
    other = make_campaign(client=make_client())
    api = api_for("manager", make_client())
    assert api.get(url(other)).status_code == 404
    assert api.put(url(other), {"windows": {}}, format="json").status_code == 404


# ------------------------------------------------------------------ the dashboard


def test_the_dashboard_trigger_filter_and_count_follow_the_windows() -> None:
    client = make_client()
    campaign = make_campaign(client=client)
    company = make_company(campaign=campaign)
    event = timezone.now().date() - timedelta(days=200)
    signal(company, "funding", event)  # fresh for 365 days by default
    api = api_for("viewer", client)
    accounts = f"/api/v1/campaigns/{campaign.pk}/accounts/"

    def rows(**params: str) -> list[str]:
        body = api.get(accounts, params).json()
        return [row["id"] for row in (body["results"] if isinstance(body, dict) else body)]

    assert rows(trigger="yes") == [str(company.pk)]
    assert api.get(accounts + "summary/").json()["trigger_yes"] == 1
    campaign_services.set_trigger_windows(campaign, {"funding": 100})
    assert rows(trigger="yes") == []
    assert rows(trigger="no") == [str(company.pk)]
    assert api.get(accounts + "summary/").json()["trigger_yes"] == 0
