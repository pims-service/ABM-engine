"""Hunter company-data adapter: the profile of a company from its domain.

A second face of the Hunter connection people may already have for finding emails: the same API
key, the same host, a different endpoint. Checked against Hunter's own API documentation
(https://hunter.io/api-documentation/v2#company-enrichment) on 2026-11-01:

* ``GET https://api.hunter.io/v2/companies/find?domain=...``. The key goes in the ``X-API-KEY``
  header. ``200`` answers ``{"data": {...}, "meta": {"domain"}}`` and ``404`` means "no
  information found for the domain". 15 requests a second and 500 a minute.
* Fields used: ``name``, ``legalName``, ``domain``, ``description``, ``category`` (``sector``,
  ``industryGroup``, ``industry``), ``geo`` (``city``, ``country``, ``countryCode``),
  ``location``, ``metrics.employees`` (a range such as ``"11-50"``), ``metrics.employeesCount``
  and ``metrics.raised``.

What Hunter does not give, and so stays unknown: headcount by department, growth, job postings,
signals and search. Funding is only a total, so no round is claimed.

NOT verified against a live key (none was available): the credit cost of a lookup (the docs do
not state one, so it is recorded as unknown and not guessed), the unit of ``metrics.raised`` (the
amount is kept without a currency), and whether ``metrics.employeesCount`` is always present.
The connection test is Hunter's free ``GET /account``, as for the email finder.

The ``Transport`` is injectable; tests never use the network.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar
from urllib.parse import urlencode, urlsplit

from apps.integrations.adapters.anthropic.transport import (
    HttpRequest,
    HttpResponse,
    Transport,
    UrllibTransport,
)
from apps.integrations.adapters.hunter.adapter import API_HOST, DEFAULT_API_BASE
from apps.integrations.framework import (
    AdapterCapabilities,
    AdapterResult,
    CompanyDataAdapter,
    CompanyRef,
    ConfigIssue,
    ConfigValidation,
    ConnectionTestResult,
    NormalizedCompany,
    ProviderBadRequest,
    ProviderInvalidOutput,
    ProviderUpstreamError,
    SecretStr,
    map_http_status,
)
from apps.integrations.framework.categories import ENRICH_COMPANY, FUNDING
from apps.integrations.framework.normalized import Funding

_DOMAIN = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class HunterCompanyAdapter(CompanyDataAdapter):
    """Company enrichment by domain from Hunter."""

    key = "hunter"
    display_name = "Hunter"
    version = "1.0.0"
    terms = "licensed_api"
    capabilities = AdapterCapabilities(
        features=frozenset({ENRICH_COMPANY, FUNDING}),
        requires_credentials=True,
        allowed_hosts=(API_HOST,),
        test_connection_costs_credits=False,
    )
    config_schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "api_base": {
                "type": "string",
                "default": DEFAULT_API_BASE,
                "maxLength": 200,
                "description": "HTTPS base URL; the host must be on the allow list.",
            },
            "timeout_seconds": {
                "type": "number",
                "default": 30,
                "minimum": 1,
                "maximum": 120,
                "description": "Connect and read timeout per request.",
            },
        },
        "additionalProperties": False,
        "x-credentials": {"required": ["api_key"], "optional": []},
    }

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        credentials: Mapping[str, SecretStr | str] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        transport: Transport | None = None,
    ) -> None:
        super().__init__(config, credentials, clock=clock)
        self._transport: Transport = transport or UrllibTransport(
            self.capabilities.allowed_hosts, provider_name=self.key
        )

    @classmethod
    def validate_config(
        cls, config: Mapping[str, Any], credentials: Mapping[str, SecretStr | str] | None = None
    ) -> ConfigValidation:
        base = super().validate_config(config, credentials)
        issues = list(base.issues)
        api_base = config.get("api_base")
        if isinstance(api_base, str) and not any(i.field == "api_base" for i in issues):
            parts = urlsplit(api_base)
            try:
                port = parts.port
            except ValueError:
                port = -1
            if (
                parts.scheme != "https"
                or (parts.hostname or "").lower() not in cls.capabilities.allowed_hosts
                or parts.username is not None
                or port not in (None, 443)
                or parts.query
                or parts.fragment
            ):
                issues.append(ConfigIssue("api_base", "enum"))
        return ConfigValidation(tuple(issues))

    # ------------------------------------------------------------------ requests
    def _get(self, path: str, query: Mapping[str, str] | None = None) -> HttpResponse:
        url = str(self.config["api_base"]).rstrip("/") + path
        if query:
            url += "?" + urlencode(query)
        headers = {"x-api-key": self.credential("api_key").reveal(), "accept": "application/json"}
        request = HttpRequest(
            "GET", url, headers, None, timeout=float(self.config["timeout_seconds"])
        )
        return self._transport.send(request)

    def _json(self, response: HttpResponse) -> dict[str, Any]:
        try:
            decoded = json.loads(response.body)
        except ValueError:
            raise ProviderUpstreamError(provider_name=self.key) from None
        if not isinstance(decoded, dict):
            raise ProviderUpstreamError(provider_name=self.key)
        return decoded

    def _raise_for(self, response: HttpResponse) -> None:
        # As for Hunter's email finder: the docs describe 403 as "you reached the rate limit".
        status = 429 if response.status == 403 else response.status
        raise map_http_status(
            status, provider_name=self.key, retry_after=response.headers.get("retry-after")
        )

    # ------------------------------------------------------------------ operations
    def test_connection(self) -> ConnectionTestResult:
        """``GET /account``: authenticates the key. The call is free."""

        def probe() -> None:
            response = self._get("/account")
            if not 200 <= response.status < 300:
                self._raise_for(response)
            if not isinstance(self._json(response).get("data"), dict):
                raise ProviderUpstreamError(provider_name=self.key)

        return self.timed_connection_test(probe)

    def enrich_company(self, company: CompanyRef) -> AdapterResult[NormalizedCompany]:
        """The profile for ``company.domain`` (Hunter looks companies up by domain only)."""
        host = (company.domain or "").strip().lower().removeprefix("www.")
        if not _DOMAIN.match(host):
            raise ProviderBadRequest("domain", provider_name=self.key)
        provenance = self.provenance(source_ref=f"companies/find:{host}")
        response = self._get("/companies/find", {"domain": host})
        if response.status == 404:
            return AdapterResult(data=None, provenance=provenance)
        if not 200 <= response.status < 300:
            self._raise_for(response)
        data = self._json(response).get("data")
        if data is None:
            return AdapterResult(data=None, provenance=provenance)
        if not isinstance(data, Mapping):
            raise ProviderUpstreamError(provider_name=self.key)
        name = _text(data.get("name")) or _text(data.get("legalName"))
        if name is None:
            return AdapterResult(data=None, provenance=provenance)
        category = _mapping(data.get("category"))
        geo = _mapping(data.get("geo"))
        metrics = _mapping(data.get("metrics"))
        place = ", ".join(
            part for part in (_text(geo.get("city")), _text(geo.get("country"))) if part
        )
        raised = metrics.get("raised")
        funding = (
            Funding(total_raised=Decimal(str(raised)))
            if isinstance(raised, (int, float)) and not isinstance(raised, bool) and raised > 0
            else None
        )
        try:
            company_data = NormalizedCompany(
                name=name,
                provider_id=_text(data.get("id")),
                domain=_text(data.get("domain")) or host,
                country=_text(geo.get("countryCode")),
                industry=_text(category.get("industry")) or _text(category.get("sector")),
                description=_text(data.get("description")),
                headquarters=_text(data.get("location")) or place or None,
                employee_count=_count(metrics.get("employeesCount")),
                employee_range=_text(metrics.get("employees")),
                funding=funding,
            )
        except ValueError:
            raise ProviderInvalidOutput(provider_name=self.key) from None
        return AdapterResult(data=company_data, provenance=provenance)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.category.value}:{self.key}>"


__all__ = ["HunterCompanyAdapter"]
