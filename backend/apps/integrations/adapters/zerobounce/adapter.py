"""ZeroBounce email verification adapter: is this address deliverable? (#84)

Checked against ZeroBounce's own API documentation on 2026-10-23
(https://www.zerobounce.net/docs/email-validation-api-quickstart/):

* ``POST https://api.zerobounce.net/v2/validate`` (the docs say POST is accepted since 3
  September 2026) with a form-encoded body ``api_key``, ``email``, ``timeout``. The key goes in
  the BODY, never in the URL, so it cannot end up in a log or a proxy. EU and US hosts exist
  (``api-eu`` / ``api-us``) and are chosen with the ``region`` setting.
* The answer carries ``status`` (``valid``, ``invalid``, ``catch-all``, ``unknown``, ``spamtrap``,
  ``abuse``, ``do_not_mail``), a ``sub_status`` (for example ``role_based``, ``disposable``,
  ``mailbox_not_found``) and ``mx_found``. An unusable key or an empty balance answers with an
  ``error`` field. The docs say an ``unknown`` result never costs a credit.
* ``POST /v2/getcredits`` -> ``{"Credits": n}``, and ``-1`` means the key is invalid (the
  connection test; it costs nothing).

NOT verified against a live account (no API key available here). The mapping into our vocabulary is
deliberately careful: only ZeroBounce's ``valid`` becomes ``valid``; a spam trap, an abuse address
and a "do not mail" address are never reported as deliverable.

The transport is the same injectable, host-allow-listed, TLS-verifying one the other adapters use;
tests never touch the network.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar
from urllib.parse import urlencode

from apps.integrations.adapters.anthropic.transport import (
    HttpRequest,
    HttpResponse,
    Transport,
    UrllibTransport,
)
from apps.integrations.framework import (
    AdapterCapabilities,
    AdapterResult,
    ConnectionTestResult,
    EmailVerification,
    EmailVerificationAdapter,
    ProviderAuthError,
    ProviderBadRequest,
    ProviderQuotaExceeded,
    ProviderUpstreamError,
    SecretStr,
    Usage,
    map_http_status,
)
from apps.integrations.framework.categories import VERIFY_EMAIL

HOSTS = {
    "default": "api.zerobounce.net",
    "us": "api-us.zerobounce.net",
    "eu": "api-eu.zerobounce.net",
}
ALLOWED_HOSTS = tuple(HOSTS.values())

_STATUS = {
    "valid": "valid",
    "invalid": "invalid",
    "catch-all": "catch_all",
    "unknown": "unknown",
    "spamtrap": "invalid",
    "abuse": "risky",
    "do_not_mail": "risky",
}


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return None


class ZeroBounceAdapter(EmailVerificationAdapter):
    """Verify one address at a time (a credit per known result)."""

    key = "zerobounce"
    display_name = "ZeroBounce"
    version = "1.0.0"
    terms = "licensed_api"
    capabilities = AdapterCapabilities(
        features=frozenset({VERIFY_EMAIL}),
        requires_credentials=True,
        allowed_hosts=ALLOWED_HOSTS,
        test_connection_costs_credits=False,
    )
    config_schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "region": {
                "type": "string",
                "enum": list(HOSTS),
                "default": "default",
                "description": "Which ZeroBounce server to use: default, us or eu.",
            },
            "timeout_seconds": {
                "type": "number",
                "default": 40,
                "minimum": 5,
                "maximum": 120,
                "description": "Read timeout per request (ZeroBounce can take up to 30 seconds).",
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

    def _post(self, path: str, fields: Mapping[str, str]) -> Any:
        body = urlencode({**fields, "api_key": self.credential("api_key").reveal()}).encode()
        request = HttpRequest(
            "POST",
            f"https://{HOSTS[str(self.config['region'])]}{path}",
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            body,
            timeout=float(self.config["timeout_seconds"]),
        )
        response: HttpResponse = self._transport.send(request)
        if not 200 <= response.status < 300:
            raise map_http_status(
                response.status,
                provider_name=self.key,
                retry_after=response.headers.get("retry-after"),
            )
        try:
            return json.loads(response.body)
        except ValueError:
            raise ProviderUpstreamError(provider_name=self.key) from None

    def test_connection(self) -> ConnectionTestResult:
        """``getcredits``: a key that is not accepted answers ``-1``. Costs no credit."""

        def probe() -> None:
            data = self._post("/v2/getcredits", {})
            credits = data.get("Credits") if isinstance(data, dict) else None
            if not isinstance(credits, int):
                raise ProviderUpstreamError(provider_name=self.key)
            if credits < 0:
                raise ProviderAuthError(provider_name=self.key)

        return self.timed_connection_test(probe)

    def verify(self, email: str) -> AdapterResult[EmailVerification]:
        address = (email or "").strip()
        if "@" not in address or len(address) > 320:
            raise ProviderBadRequest("email", provider_name=self.key)
        data = self._post("/v2/validate", {"email": address, "timeout": "30"})
        if not isinstance(data, dict):
            raise ProviderUpstreamError(provider_name=self.key)
        if "error" in data and "status" not in data:
            # "Invalid API key or your account ran out of credits": the docs do not say which.
            raise ProviderQuotaExceeded(provider_name=self.key)
        raw_status = str(data.get("status", "")).lower()
        sub = str(data.get("sub_status", "")).lower()
        status = _STATUS.get(raw_status, "unknown")
        if raw_status == "do_not_mail" and sub == "disposable":
            status = "disposable"
        verdict = EmailVerification(
            email=address,
            status=status,
            reason=sub or None,
            is_role_account=True if sub.startswith("role_based") else None,
            is_disposable=True if sub == "disposable" else None,
            is_catch_all=True if raw_status == "catch-all" or "accept_all" in sub else None,
            mx_found=_flag(data.get("mx_found")),
        )
        return AdapterResult(
            data=verdict,
            provenance=self.provenance(source_ref="validate"),
            usage=Usage(credits=Decimal(0) if status == "unknown" else Decimal(1)),
        )
