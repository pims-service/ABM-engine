"""Hunter adapter: find a person's work email from a name and a company domain.

Checked against Hunter's own API documentation (https://hunter.io/api-documentation/v2) on
2026-10-31:

* Base ``https://api.hunter.io/v2``. The key goes in the ``X-API-KEY`` header (the docs also allow
  an ``api_key`` query parameter and a Bearer header; the header keeps the key out of URLs).
* Email Finder: ``GET /email-finder?domain=...&first_name=...&last_name=...``. ``domain`` and the
  name are required (first and last name together, or ``full_name``). A hit answers
  ``{"data": {"email", "score", "first_name", "last_name", "position", "verification":
  {"status"}, "sources": [...]}}``. "If no email can be found, no credit is charged."
* Account: ``GET /account`` answers ``{"data": {"email", "plan_name", "requests": {...}}}`` and
  "this call is free" (the connection test).
* Errors: ``400`` (a wrong or missing parameter, including a domain with no mail server),
  ``401`` (no valid key), ``403`` (the docs say: the rate limit was reached), ``429`` (the usage
  limit was reached), ``451`` (the person asked not to be processed), ``5xx``.

NOT verified against a live key (none was available): the body and status Hunter answers when it
finds nothing (a ``200`` with ``email: null`` and a ``404`` are both treated as "found nothing",
a normal answer and not an error), the exact credit cost of a hit (taken as 1, an inference from
the docs' "a search costs 1 credit"), and the error JSON.

The email is never marked verified here. Hunter's score is kept as a confidence and the status
stays "unknown" until a verifier says otherwise, as for every email finder in this app.

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
from apps.integrations.framework import (
    AdapterCapabilities,
    AdapterResult,
    ConfigIssue,
    ConfigValidation,
    ConnectionTestResult,
    EmailEnrichmentAdapter,
    EmailFinding,
    PersonRef,
    ProviderBadRequest,
    ProviderUpstreamError,
    SecretStr,
    Usage,
    map_http_status,
)
from apps.integrations.framework.categories import FIND_EMAIL

API_HOST = "api.hunter.io"
DEFAULT_API_BASE = f"https://{API_HOST}/v2"

_DOMAIN = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _split_name(full_name: str) -> tuple[str, str] | None:
    """``(first, last)`` of a full name, or ``None`` for a single word (Hunter needs both)."""
    words = full_name.replace(",", " ").split()
    if len(words) < 2:
        return None
    return words[0], words[-1]


class HunterAdapter(EmailEnrichmentAdapter):
    """A person's work email by name and company domain (one credit when found)."""

    key = "hunter"
    display_name = "Hunter"
    version = "1.0.0"
    terms = "licensed_api"
    capabilities = AdapterCapabilities(
        features=frozenset({FIND_EMAIL}),
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
        # The docs describe 403 as "you reached the rate limit", so it is retried like a 429.
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

    def find_email(self, person: PersonRef, domain: str) -> AdapterResult[EmailFinding]:
        """By name and company domain; ``person.profile_url`` is not needed."""
        host = (domain or "").strip().lower().removeprefix("www.")
        if not _DOMAIN.match(host):
            raise ProviderBadRequest("domain", provider_name=self.key)
        name = _split_name(person.full_name)
        if name is None:
            raise ProviderBadRequest("full_name", provider_name=self.key)
        response = self._get(
            "/email-finder",
            {"domain": host, "first_name": name[0], "last_name": name[1]},
        )
        provenance = self.provenance(source_ref=f"email-finder:{host}")
        if response.status in (404, 451):
            # Nothing to find, or the person asked not to be processed: a normal answer.
            return AdapterResult(
                data=EmailFinding(email=None),
                provenance=provenance,
                usage=Usage(credits=Decimal(0)),
            )
        if not 200 <= response.status < 300:
            self._raise_for(response)
        data = self._json(response).get("data")
        if not isinstance(data, Mapping):
            raise ProviderUpstreamError(provider_name=self.key)
        email = data.get("email")
        email = email.strip() if isinstance(email, str) and email.strip() else None
        score = data.get("score")
        confidence = (
            Decimal(score) / Decimal(100)
            if isinstance(score, int) and not isinstance(score, bool) and 0 <= score <= 100
            else None
        )
        return AdapterResult(
            data=EmailFinding(email=email, status="unknown", confidence=confidence),
            provenance=provenance,
            usage=Usage(credits=Decimal(1) if email else Decimal(0)),
        )

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.category.value}:{self.key}>"


__all__ = ["HunterAdapter"]
