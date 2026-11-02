"""Company-data adapters (Apollo, People Data Labs, Hunter): the shared contract and each
provider's request shape, mapping and errors. Fake transport only: no test uses the network."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any, ClassVar

import pytest

from apps.imports.providers.types import CompanySearchRequest
from apps.integrations.adapters.anthropic.transport import HttpRequest, HttpResponse
from apps.integrations.adapters.apollo import ApolloAdapter
from apps.integrations.adapters.hunter import HunterCompanyAdapter
from apps.integrations.adapters.peopledatalabs import PeopleDataLabsAdapter
from apps.integrations.adapters.peopledatalabs.adapter import growth_percent
from apps.integrations.framework import (
    CompanyRef,
    ProviderAuthError,
    ProviderBadRequest,
    ProviderError,
    ProviderQuotaExceeded,
    ProviderRateLimited,
)
from tests.adapter_contract import CompanyDataContract

pytestmark = pytest.mark.unit

KEY = "YOUR_API_KEY"  # pragma: allowlist secret


class FakeTransport:
    """Records requests; answers with ``responder(request)`` or a fixed status and body."""

    def __init__(
        self,
        body: Any = None,
        status: int = 200,
        headers: dict[str, str] | None = None,
        responder: Callable[[HttpRequest], HttpResponse] | None = None,
    ) -> None:
        self.requests: list[HttpRequest] = []
        self._responder = responder
        self._fixed = HttpResponse(status, headers or {}, json.dumps(body or {}).encode())

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self._responder(request) if self._responder else self._fixed


def reply(body: Any, status: int = 200) -> HttpResponse:
    return HttpResponse(status, {}, json.dumps(body).encode())


# ====================================================================== Apollo
APOLLO_ORG = {
    "id": "org-1",
    "name": "Acme Logistics",
    "website_url": "http://www.acme.example.com",
    "primary_domain": "acme.example.com",
    "linkedin_url": "http://www.linkedin.com/company/acme",
    "industry": "logistics and supply chain",
    "short_description": "Regional freight forwarder.",
    "estimated_num_employees": 1600,
    "city": "Riyadh",
    "state": "Riyadh Province",
    "country": "Saudi Arabia",
    "departmental_head_count": {
        "sales": 177,
        "marketing": 36,
        "business_development": 14,
        "engineering": 228,
    },
    "organization_headcount_six_month_growth": 12.3,
    "organization_headcount_twelve_month_growth": 24.6,
    "organization_headcount_twenty_four_month_growth": 48.9,
    "total_funding": 251200000,
    "latest_funding_stage": "Series D",
    "latest_funding_round_date": "2023-08-01T00:00:00.000+00:00",
    "funding_events": [
        {
            "date": "2021-01-01T00:00:00.000+00:00",
            "type": "Series C",
            "amount": "60M",
            "currency": "$",
        },
        {
            "date": "2023-08-01T00:00:00.000+00:00",
            "type": "Series D",
            "amount": "100M",
            "currency": "$",
        },
    ],
}


def apollo_routed() -> FakeTransport:
    def responder(request: HttpRequest) -> HttpResponse:
        if "no-such" in request.url:
            return reply({"organization": None})
        if "/organizations/enrich" in request.url:
            return reply({"organization": APOLLO_ORG})
        if "/job_postings" in request.url:
            return reply({"organization_job_postings": []})
        if "/mixed_companies/search" in request.url:
            return reply(
                {
                    "organizations": [APOLLO_ORG],
                    "pagination": {"page": 1, "per_page": 5, "total_entries": 1, "total_pages": 1},
                }
            )
        return reply({}, 404)

    return FakeTransport(responder=responder)


def apollo_with(transport: FakeTransport, **config: Any) -> ApolloAdapter:
    return ApolloAdapter(config, {"api_key": KEY}, transport=transport)


class TestApolloContract(CompanyDataContract):
    adapter_cls = ApolloAdapter

    def make_adapter(self) -> Any:
        return self.adapter_cls({}, self.make_credentials(), transport=apollo_routed())

    def make_failing_adapter(self, error: ProviderError) -> Any:
        transport = FakeTransport(status=429, headers={"retry-after": "5"})
        return self.adapter_cls({}, self.make_credentials(), transport=transport)


class TestApollo:
    def test_enrichment_request_follows_the_documented_api(self) -> None:
        transport = FakeTransport({"organization": APOLLO_ORG})
        apollo_with(transport).enrich_company(CompanyRef(domain="acme.example.com", name="Acme"))
        request = transport.requests[0]
        assert request.method == "GET"
        assert request.url == (
            "https://api.apollo.io/api/v1/organizations/enrich?domain=acme.example.com&name=Acme"
        )
        assert request.headers["x-api-key"] == KEY
        assert KEY not in request.url
        assert request.body is None

    def test_the_profile_is_mapped_and_unknown_things_stay_unknown(self) -> None:
        result = apollo_with(FakeTransport({"organization": APOLLO_ORG})).enrich_company(
            CompanyRef(domain="acme.example.com")
        )
        company = result.data
        assert company is not None
        assert (company.name, company.provider_id) == ("Acme Logistics", "org-1")
        assert company.country == "SA"
        assert company.employee_count == 1600
        assert company.headquarters == "Riyadh, Riyadh Province, Saudi Arabia"
        sales = company.department("sales")
        bd = company.department("bd")
        marketing = company.department("marketing")
        assert sales is not None
        assert bd is not None
        assert marketing is not None
        assert (sales.count, bd.count, marketing.count) == (177, 14, 36)
        assert company.department("commercial") is None
        assert company.department("partnerships") is None
        assert company.headcount_change_6m == Decimal("12.3")
        assert company.headcount_change_12m == Decimal("24.6")
        assert company.headcount_change_3m is None  # Apollo has no 3 month figure
        assert result.usage.credits == Decimal(1)
        assert result.provenance.source_ref == "organizations/enrich:acme.example.com"

    def test_funding_is_mapped_from_the_newest_round(self) -> None:
        company = (
            apollo_with(FakeTransport({"organization": APOLLO_ORG}))
            .enrich_company(CompanyRef(domain="acme.example.com"))
            .data
        )
        assert company is not None
        funding = company.funding
        assert funding is not None
        assert funding.total_raised == Decimal(251200000)
        assert funding.last_round_type == "Series D"
        assert funding.last_round_date == date(2023, 8, 1)
        assert funding.last_round_amount == Decimal(100_000_000)
        assert funding.currency == "USD"

    @pytest.mark.parametrize(
        "response",
        [
            HttpResponse(404, {}, b"{}"),
            HttpResponse(200, {}, b'{"organization": null}'),
            HttpResponse(200, {}, b'{"organization": {"name": ""}}'),
        ],
    )
    def test_an_unknown_company_is_none_and_free(self, response: HttpResponse) -> None:
        transport = FakeTransport(responder=lambda request: response)
        result = apollo_with(transport).enrich_company(CompanyRef(domain="nobody.example.com"))
        assert result.data is None
        assert result.usage.credits == Decimal(0)

    def test_a_lookup_needs_something_to_look_up_by(self) -> None:
        with pytest.raises(ProviderBadRequest):
            apollo_with(FakeTransport()).enrich_company(CompanyRef(name="Acme"))

    @pytest.mark.parametrize(
        ("status", "error"),
        [(401, ProviderAuthError), (403, ProviderAuthError), (422, ProviderBadRequest)],
    )
    def test_http_errors_are_typed(self, status: int, error: type[ProviderError]) -> None:
        with pytest.raises(error):
            apollo_with(FakeTransport(status=status)).enrich_company(
                CompanyRef(domain="acme.example.com")
            )

    def test_a_rate_limit_carries_the_wait(self) -> None:
        transport = FakeTransport(status=429, headers={"retry-after": "30"})
        with pytest.raises(ProviderRateLimited) as info:
            apollo_with(transport).enrich_company(CompanyRef(domain="acme.example.com"))
        assert info.value.retry_after == 30

    def test_search_sends_the_filters_the_api_documents(self) -> None:
        transport = apollo_routed()
        request = CompanySearchRequest(
            query="Acme",
            countries=("SA",),
            industries=("logistics",),
            employee_min=10,
            employee_max=500,
            page_size=5,
        )
        apollo_with(transport).search(request)
        sent = transport.requests[0]
        assert sent.method == "POST"
        assert sent.url.startswith("https://api.apollo.io/api/v1/mixed_companies/search?")
        assert "page=1" in sent.url
        assert "per_page=5" in sent.url
        assert "q_organization_name=Acme" in sent.url
        assert "organization_locations%5B%5D=saudi+arabia" in sent.url
        assert "q_organization_keyword_tags%5B%5D=logistics" in sent.url
        assert "organization_num_employees_ranges%5B%5D=10%2C500" in sent.url

    def test_search_pages_by_number_and_counts_one_credit_a_page(self) -> None:
        body = {
            "organizations": [APOLLO_ORG],
            "pagination": {"page": 1, "per_page": 1, "total_entries": 3, "total_pages": 3},
        }
        result = apollo_with(FakeTransport(body)).search(CompanySearchRequest(page_size=1))
        assert result.data is not None
        assert result.data.next_cursor == "2"
        assert result.data.total_estimate == 3
        assert result.usage.credits == Decimal(1)
        last = apollo_with(FakeTransport(body)).search(CompanySearchRequest(cursor="3"))
        assert last.data is not None
        assert last.data.next_cursor is None

    def test_excluded_industries_are_dropped_here_because_apollo_cannot_exclude(self) -> None:
        body = {
            "organizations": [
                APOLLO_ORG,
                {**APOLLO_ORG, "id": "org-2", "name": "Gov Co", "industry": "government"},
            ],
            "pagination": {"total_pages": 1, "total_entries": 2},
        }
        result = apollo_with(FakeTransport(body)).search(
            CompanySearchRequest(excluded_industries=("Government",))
        )
        assert result.data is not None
        assert [c.name for c in result.data.items] == ["Acme Logistics"]

    def test_an_unknown_country_is_refused_before_anything_is_sent(self) -> None:
        transport = FakeTransport()
        with pytest.raises(ProviderBadRequest):
            apollo_with(transport).search(CompanySearchRequest(countries=("ZZ",)))
        assert transport.requests == []

    def test_job_postings_by_organization_id(self) -> None:
        body = {
            "organization_job_postings": [
                {
                    "id": "job-1",
                    "title": "Sales Manager",
                    "url": "https://acme.example.com/jobs/1",
                    "posted_at": "2026-10-20T00:00:00.000+00:00",
                    "city": "Riyadh",
                    "state": None,
                    "country": "Saudi Arabia",
                },
                {"id": "job-2", "title": ""},
            ]
        }
        transport = FakeTransport(body)
        result = apollo_with(transport).list_job_postings(CompanyRef(provider_id="org-1"))
        request = transport.requests[0]
        assert request.method == "GET"
        assert request.url == (
            "https://api.apollo.io/api/v1/organizations/org-1/job_postings?page=1&per_page=100"
        )
        assert result.data is not None
        assert len(result.data) == 1
        posting = result.data[0]
        assert posting.title == "Sales Manager"
        assert posting.posted_date == date(2026, 10, 20)
        assert posting.location == "Riyadh, Saudi Arabia"
        assert result.usage.credits == Decimal(1)

    def test_job_postings_without_an_id_look_the_company_up_first(self) -> None:
        def responder(request: HttpRequest) -> HttpResponse:
            if "/organizations/enrich" in request.url:
                return reply({"organization": APOLLO_ORG})
            return reply({"organization_job_postings": []})

        transport = FakeTransport(responder=responder)
        result = apollo_with(transport).list_job_postings(CompanyRef(domain="acme.example.com"))
        assert [r.url.split("?")[0].rsplit("/", 2)[-2:] for r in transport.requests] == [
            ["organizations", "enrich"],
            ["org-1", "job_postings"],
        ]
        assert result.usage.credits == Decimal(2)

    def test_the_api_host_cannot_be_changed(self) -> None:
        result = ApolloAdapter.validate_config({"api_base": "https://evil.example.com/v1"}, {})
        assert any(issue.field == "api_base" for issue in result.issues)


# ====================================================================== People Data Labs
PDL_RECORD = {
    "status": 200,
    "likelihood": 8,
    "id": "pdl-1",
    "name": "acme logistics",
    "display_name": "Acme Logistics",
    "website": "acme.example.com",
    "linkedin_url": "linkedin.com/company/acme",
    "industry": "logistics and supply chain",
    "summary": "Regional freight forwarder.",
    "size": "51-200",
    "employee_count": 88,
    "location": {"name": "riyadh, riyadh province, saudi arabia", "country": "saudi arabia"},
    "employee_count_by_role": {"sales": 16, "marketing": 5, "partnerships": 3, "engineering": 23},
    "employee_count_by_month": {"2025-09": 80, "2026-03": 100, "2026-06": 110, "2026-09": 88},
    "total_funding_raised": 55475000.0,
    "latest_funding_stage": "series_b",
    "last_funding_date": "2021-11-16",
    "funding_details": [
        {
            "funding_type": "seed",
            "funding_raised": 2000000.0,
            "funding_currency": "USD",
            "funding_round_date": "2018-05-01",
        },
        {
            "funding_type": "series_b",
            "funding_raised": 45000000.0,
            "funding_currency": "USD",
            "funding_round_date": "2021-11-16",
        },
    ],
}


def pdl_routed() -> FakeTransport:
    def responder(request: HttpRequest) -> HttpResponse:
        if "no-such" in request.url:
            return reply({"status": 404, "error": {"type": "not_found"}}, 404)
        if "/company/enrich" in request.url:
            return reply(PDL_RECORD)
        if "/company/search" in request.url:
            return reply({"status": 200, "data": [PDL_RECORD], "total": 1, "scroll_token": "t1"})
        return reply({}, 404)

    return FakeTransport(responder=responder)


def pdl_with(transport: FakeTransport, **config: Any) -> PeopleDataLabsAdapter:
    return PeopleDataLabsAdapter(config, {"api_key": KEY}, transport=transport)


class TestPeopleDataLabsContract(CompanyDataContract):
    adapter_cls = PeopleDataLabsAdapter

    def make_adapter(self) -> Any:
        return self.adapter_cls({}, self.make_credentials(), transport=pdl_routed())

    def make_failing_adapter(self, error: ProviderError) -> Any:
        transport = FakeTransport(status=429, headers={"retry-after": "5"})
        return self.adapter_cls({}, self.make_credentials(), transport=transport)


class TestPeopleDataLabs:
    def test_enrichment_request_follows_the_documented_api(self) -> None:
        transport = FakeTransport(PDL_RECORD)
        pdl_with(transport).enrich_company(CompanyRef(domain="acme.example.com"))
        request = transport.requests[0]
        assert request.method == "GET"
        assert request.url == (
            "https://api.peopledatalabs.com/v5/company/enrich"
            "?min_likelihood=6&titlecase=true&website=acme.example.com"
        )
        assert request.headers["x-api-key"] == KEY
        assert KEY not in request.url

    def test_the_provider_id_wins_and_the_likelihood_is_a_setting(self) -> None:
        transport = FakeTransport(PDL_RECORD)
        pdl_with(transport, min_likelihood=9).enrich_company(
            CompanyRef(provider_id="pdl-1", domain="acme.example.com")
        )
        assert transport.requests[0].url.endswith("?min_likelihood=9&titlecase=true&pdl_id=pdl-1")

    def test_the_profile_is_mapped(self) -> None:
        result = pdl_with(FakeTransport(PDL_RECORD)).enrich_company(
            CompanyRef(domain="acme.example.com")
        )
        company = result.data
        assert company is not None
        assert company.name == "Acme Logistics"
        assert company.country == "SA"
        assert company.employee_count == 88
        assert company.employee_range == "51-200"
        assert company.website == "https://acme.example.com"
        assert company.confidence == Decimal("0.8")
        sales = company.department("sales")
        assert sales is not None
        assert sales.count == 16
        assert company.department("bd") is None  # not in the provider's role list
        assert result.usage.credits == Decimal(1)

    def test_growth_is_worked_out_of_the_monthly_series(self) -> None:
        company = (
            pdl_with(FakeTransport(PDL_RECORD))
            .enrich_company(CompanyRef(domain="acme.example.com"))
            .data
        )
        assert company is not None
        assert company.headcount_change_3m == Decimal("-20.00")  # 110 -> 88
        assert company.headcount_change_6m == Decimal("-12.00")  # 100 -> 88
        assert company.headcount_change_12m == Decimal("10.00")  # 80 -> 88

    def test_funding_is_mapped(self) -> None:
        company = (
            pdl_with(FakeTransport(PDL_RECORD))
            .enrich_company(CompanyRef(domain="acme.example.com"))
            .data
        )
        assert company is not None
        funding = company.funding
        assert funding is not None
        assert funding.total_raised == Decimal("55475000.0")
        assert funding.last_round_type == "Series B"
        assert funding.last_round_date == date(2021, 11, 16)
        assert funding.last_round_amount == Decimal("45000000.0")
        assert funding.currency == "USD"

    def test_no_match_is_none_and_free(self) -> None:
        result = pdl_with(FakeTransport({"status": 404}, status=404)).enrich_company(
            CompanyRef(domain="nobody.example.com")
        )
        assert result.data is None
        assert result.usage.credits == Decimal(0)

    @pytest.mark.parametrize(
        ("status", "error"),
        [(401, ProviderAuthError), (402, ProviderQuotaExceeded), (400, ProviderBadRequest)],
    )
    def test_http_errors_are_typed(self, status: int, error: type[ProviderError]) -> None:
        with pytest.raises(error):
            pdl_with(FakeTransport(status=status)).enrich_company(
                CompanyRef(domain="acme.example.com")
            )

    def test_search_sends_a_query_and_pages_with_the_scroll_token(self) -> None:
        transport = pdl_routed()
        request = CompanySearchRequest(
            query="Acme",
            countries=("SA",),
            industries=("Logistics",),
            employee_min=10,
            employee_max=500,
            excluded_industries=("Government",),
            page_size=1,
            cursor="prev-token",
        )
        result = pdl_with(transport).search(request)
        sent = transport.requests[0]
        assert sent.method == "POST"
        assert sent.url == "https://api.peopledatalabs.com/v5/company/search"
        assert sent.headers["content-type"] == "application/json"
        assert sent.body is not None
        body = json.loads(sent.body)
        assert body["size"] == 1
        assert body["scroll_token"] == "prev-token"
        must = body["query"]["bool"]["must"]
        assert {"match": {"name": "Acme"}} in must
        assert {"terms": {"location.country": ["saudi arabia"]}} in must
        assert {"terms": {"industry": ["logistics"]}} in must
        assert {"range": {"employee_count": {"gte": 10, "lte": 500}}} in must
        assert body["query"]["bool"]["must_not"] == [{"terms": {"industry": ["government"]}}]
        assert result.data is not None
        assert result.data.next_cursor == "t1"  # a full page: there may be more
        assert result.data.total_estimate == 1
        assert result.usage.credits == Decimal(1)  # one record returned, one credit

    def test_a_short_page_ends_the_paging(self) -> None:
        result = pdl_with(pdl_routed()).search(CompanySearchRequest(query="Acme", page_size=5))
        assert result.data is not None
        assert result.data.next_cursor is None

    def test_an_unknown_country_is_refused_before_anything_is_sent(self) -> None:
        transport = FakeTransport()
        with pytest.raises(ProviderBadRequest):
            pdl_with(transport).search(CompanySearchRequest(countries=("ZZ",)))
        assert transport.requests == []

    def test_the_connection_test_uses_a_company_that_does_not_exist(self) -> None:
        transport = pdl_routed()
        assert pdl_with(transport).test_connection().ok is True
        assert "no-such-company" in transport.requests[0].url
        rejected = pdl_with(FakeTransport(status=401)).test_connection()
        assert rejected.ok is False
        assert rejected.error_code == "provider_auth"


class TestGrowthPercent:
    series: ClassVar[dict[str, int]] = {"2025-09": 80, "2026-03": 100, "2026-09": 88}

    def test_the_latest_month_against_the_month_before_the_window(self) -> None:
        assert growth_percent(self.series, 6) == Decimal("-12.00")
        assert growth_percent(self.series, 12) == Decimal("10.00")

    def test_a_missing_month_a_zero_and_junk_are_unknown(self) -> None:
        assert growth_percent(self.series, 3) is None
        assert growth_percent({"2026-03": 0, "2026-09": 5}, 6) is None
        assert growth_percent({"nonsense": 5, "2026-09": "x"}, 6) is None
        assert growth_percent(None, 6) is None
        assert growth_percent({}, 6) is None


# ====================================================================== Hunter
HUNTER_COMPANY = {
    "data": {
        "id": "h-1",
        "name": "Acme Logistics",
        "domain": "acme.example.com",
        "description": "Regional freight forwarder.",
        "category": {"sector": "Industrials", "industry": "Air Freight & Logistics"},
        "geo": {"city": "Riyadh", "country": "Saudi Arabia", "countryCode": "SA"},
        "metrics": {"employees": "51-200", "employeesCount": 120, "raised": 5000000},
    },
    "meta": {"domain": "acme.example.com"},
}


def hunter_routed() -> FakeTransport:
    def responder(request: HttpRequest) -> HttpResponse:
        if "/account" in request.url:
            return reply({"data": {"email": "owner@hunter.example.com", "plan_name": "Free"}})
        if "no-such" in request.url:
            return reply({"errors": []}, 404)
        return reply(HUNTER_COMPANY)

    return FakeTransport(responder=responder)


def hunter_with(transport: FakeTransport) -> HunterCompanyAdapter:
    return HunterCompanyAdapter({}, {"api_key": KEY}, transport=transport)


class TestHunterCompanyContract(CompanyDataContract):
    adapter_cls = HunterCompanyAdapter

    def make_adapter(self) -> Any:
        return self.adapter_cls({}, self.make_credentials(), transport=hunter_routed())

    def make_failing_adapter(self, error: ProviderError) -> Any:
        transport = FakeTransport(status=429, headers={"retry-after": "5"})
        return self.adapter_cls({}, self.make_credentials(), transport=transport)


class TestHunterCompany:
    def test_request_follows_the_documented_api(self) -> None:
        transport = FakeTransport(HUNTER_COMPANY)
        hunter_with(transport).enrich_company(CompanyRef(domain="WWW.Acme.Example.com"))
        request = transport.requests[0]
        assert request.method == "GET"
        assert request.url == "https://api.hunter.io/v2/companies/find?domain=acme.example.com"
        assert request.headers["x-api-key"] == KEY
        assert KEY not in request.url

    def test_the_profile_is_mapped_and_the_rest_stays_unknown(self) -> None:
        result = hunter_with(FakeTransport(HUNTER_COMPANY)).enrich_company(
            CompanyRef(domain="acme.example.com")
        )
        company = result.data
        assert company is not None
        assert company.name == "Acme Logistics"
        assert company.country == "SA"
        assert company.industry == "Air Freight & Logistics"
        assert company.employee_count == 120
        assert company.employee_range == "51-200"
        assert company.headquarters == "Riyadh, Saudi Arabia"
        assert company.funding is not None
        assert company.funding.total_raised == Decimal(5000000)
        assert company.funding.currency is None  # the unit is not documented, so not claimed
        assert company.departments == ()
        assert company.headcount_change_6m is None
        assert result.usage.credits is None  # the cost is not documented

    def test_an_unknown_domain_is_none(self) -> None:
        result = hunter_with(FakeTransport({}, status=404)).enrich_company(
            CompanyRef(domain="nobody.example.com")
        )
        assert result.data is None

    def test_a_company_without_a_domain_is_refused(self) -> None:
        with pytest.raises(ProviderBadRequest):
            hunter_with(FakeTransport()).enrich_company(CompanyRef(name="Acme"))

    def test_403_is_the_rate_limit_as_the_docs_describe(self) -> None:
        with pytest.raises(ProviderRateLimited):
            hunter_with(FakeTransport(status=403)).enrich_company(
                CompanyRef(domain="acme.example.com")
            )

    def test_the_connection_test_is_the_free_account_call(self) -> None:
        transport = hunter_routed()
        assert hunter_with(transport).test_connection().ok is True
        assert transport.requests[0].url.endswith("/v2/account")
