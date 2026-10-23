"""SignalHire adapters: find the people at a company and find a person's work email (#83, #84).

Checked against SignalHire's own API documentation on 2026-10-23 (https://docs.signalhire.com):

* Base ``https://www.signalhire.com/api/v1``; every request carries an ``apikey`` header.
* People search: ``POST /candidate/searchByQuery`` with boolean ``currentCompany`` and
  ``currentTitle`` queries and ``size`` (1 to 100). Answers ``{"requestId", "total", "profiles":
  [{"uid", "fullName", "location", "experience": [{"company", "title"}], ...}]}``. Searches cost no
  credits; they count against a daily quota shared with the website (``402`` when it is used up).
  At most 3 concurrent searches (``429``). The profiles carry no email and no LinkedIn URL.
* Reveal: ``POST /candidate/search`` with ``{"items": [<uid or LinkedIn URL>], "withoutWaterfall":
  true}`` answers at once (no callback URL needed) with ``[{"item", "status", "candidate":
  {"fullName", "contacts": [{"type": "email", "value", "rating", "subType"}], ...}}]``. A
  successful match costs one credit. ``status`` is ``success``, ``failed``, ``credits_are_over``
  or ``duplicate_query``. The docs say the synchronous mode has less current data and lower contact
  coverage than the callback mode, which this adapter does not use (it needs a public URL).
* Credits: ``GET /credits`` -> ``{"credits": n}`` (the connection test).

NOT verified against a live account (no API key available here). Searching matches the company by
its name as text, so the adapter keeps only profiles whose experience names the company asked for,
and says how many it dropped. Emails are never marked verified: the provider's rating is kept as a
confidence and the status stays "unknown" until a verifier says otherwise.

The transport is the same injectable, host-allow-listed, TLS-verifying one the other adapters use;
tests never touch the network.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar
from urllib.parse import urlsplit

from apps.integrations.adapters.anthropic.transport import (
    HttpRequest,
    HttpResponse,
    Transport,
    UrllibTransport,
)
from apps.integrations.framework import (
    AdapterCapabilities,
    AdapterResult,
    CompanyRef,
    ConfigIssue,
    ConfigValidation,
    ConnectionTestResult,
    EmailEnrichmentAdapter,
    EmailFinding,
    NormalizedPerson,
    PeopleDataAdapter,
    PersonRef,
    ProviderBadRequest,
    ProviderUpstreamError,
    SecretStr,
    Usage,
    map_http_status,
)
from apps.integrations.framework.categories import FIND_EMAIL, FIND_PEOPLE
from apps.integrations.framework.operations import clean_roles

API_HOST = "www.signalhire.com"
DEFAULT_API_BASE = f"https://{API_HOST}/api/v1"
UID = re.compile(r"[A-Za-z0-9]{32}")
LINKEDIN = re.compile(r"https://([a-z]{2,3}\.)?linkedin\.com/in/[^\s]+", re.IGNORECASE)
_CONFIG_SCHEMA: Mapping[str, Any] = {
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


def _quoted(text: str) -> str:
    """One boolean-query phrase: quotes inside the text would end the phrase, so they go."""
    return '"' + text.replace('"', " ").strip() + '"'


def _same_company(wanted: str, found: str | None) -> bool:
    def norm(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    a, b = norm(wanted), norm(found or "")
    return bool(a and b and (a == b or a in b or b in a))


class _SignalHireHttp:
    """What both SignalHire adapters share: the request, the status mapping, the config rules."""

    key: ClassVar[str] = "signalhire"
    display_name: ClassVar[str] = "SignalHire"
    version: ClassVar[str] = "1.0.0"
    terms: ClassVar[str] = "licensed_api"
    config_schema: ClassVar[Mapping[str, Any]] = _CONFIG_SCHEMA

    config: Mapping[str, Any]
    _transport: Transport

    def _setup(self, transport: Transport | None) -> None:
        self._transport = transport or UrllibTransport((API_HOST,), provider_name="signalhire")

    @staticmethod
    def _validate_base(config: Mapping[str, Any], issues: list[ConfigIssue]) -> None:
        api_base = config.get("api_base")
        if isinstance(api_base, str) and not any(i.field == "api_base" for i in issues):
            parts = urlsplit(api_base)
            try:
                port = parts.port
            except ValueError:
                port = -1
            if (
                parts.scheme != "https"
                or (parts.hostname or "").lower() != API_HOST
                or parts.username is not None
                or port not in (None, 443)
                or parts.query
                or parts.fragment
            ):
                issues.append(ConfigIssue("api_base", "enum"))

    def _send(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> HttpResponse:
        headers = {
            "apikey": self.credential("api_key").reveal(),  # type: ignore[attr-defined]
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
        request = HttpRequest(
            method,
            str(self.config["api_base"]).rstrip("/") + path,
            headers,
            body,
            timeout=float(self.config["timeout_seconds"]),
        )
        response = self._transport.send(request)
        if not 200 <= response.status < 300:
            raise map_http_status(
                response.status,
                provider_name="signalhire",
                retry_after=response.headers.get("retry-after"),
            )
        return response

    def _json(self, response: HttpResponse) -> Any:
        try:
            return json.loads(response.body)
        except ValueError:
            raise ProviderUpstreamError(provider_name="signalhire") from None

    def _probe_credits(self) -> None:
        data = self._json(self._send("GET", "/credits"))
        if not isinstance(data, dict) or not isinstance(data.get("credits"), int):
            raise ProviderUpstreamError(provider_name="signalhire")


def _capabilities(feature: str) -> AdapterCapabilities:
    return AdapterCapabilities(
        features=frozenset({feature}),
        requires_credentials=True,
        allowed_hosts=(API_HOST,),
        test_connection_costs_credits=False,
    )


class SignalHirePeopleAdapter(_SignalHireHttp, PeopleDataAdapter):
    """People at a company by title (search; costs no credits, only the daily search quota)."""

    capabilities = _capabilities(FIND_PEOPLE)

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        credentials: Mapping[str, SecretStr | str] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        transport: Transport | None = None,
    ) -> None:
        PeopleDataAdapter.__init__(self, config, credentials, clock=clock)
        self._setup(transport)

    @classmethod
    def validate_config(
        cls, config: Mapping[str, Any], credentials: Mapping[str, SecretStr | str] | None = None
    ) -> ConfigValidation:
        base = super().validate_config(config, credentials)
        issues = list(base.issues)
        cls._validate_base(config, issues)
        return ConfigValidation(tuple(issues))

    def test_connection(self) -> ConnectionTestResult:
        """``GET /credits``: shows the key is accepted (no credit is used)."""
        return self.timed_connection_test(self._probe_credits)

    def find_people(
        self, company: CompanyRef, roles: Sequence[str], *, limit: int = 10
    ) -> AdapterResult[tuple[NormalizedPerson, ...]]:
        wanted = clean_roles(roles)
        if not company.name:
            raise ProviderBadRequest("company", provider_name=self.key)
        size = min(max(int(limit), 1), 100)
        body = {
            "currentCompany": _quoted(company.name),
            "currentTitle": "(" + " OR ".join(_quoted(r) for r in wanted) + ")",
            "size": size,
        }
        response = self._send("POST", "/candidate/searchByQuery", body)
        data = self._json(response)
        profiles = data.get("profiles") if isinstance(data, dict) else None
        if not isinstance(profiles, list):
            raise ProviderUpstreamError(provider_name=self.key)
        people: list[NormalizedPerson] = []
        dropped = 0
        for profile in profiles:
            person = self._person(profile, company)
            if person is None:
                dropped += 1
            else:
                people.append(person)
        warnings = (
            (f"{dropped} profiles were left out because they do not name this company.",)
            if dropped
            else ()
        )
        request_id = data.get("requestId")
        return AdapterResult(
            data=tuple(people[:size]),
            provenance=self.provenance(
                source_ref=f"search:{request_id}" if request_id else "search",
                request_id=str(request_id) if request_id else None,
            ),
            warnings=warnings,
        )

    @staticmethod
    def _person(profile: Any, company: CompanyRef) -> NormalizedPerson | None:
        if not isinstance(profile, dict):
            return None
        name, uid = profile.get("fullName"), profile.get("uid")
        if not isinstance(name, str) or not isinstance(uid, str) or not name.strip():
            return None
        experience = profile.get("experience")
        title = None
        for entry in experience if isinstance(experience, list) else []:
            if isinstance(entry, dict) and _same_company(company.name or "", entry.get("company")):
                title = entry.get("title") if isinstance(entry.get("title"), str) else None
                break
        else:
            return None
        return NormalizedPerson(
            name=name,
            title=title,
            company_domain=company.domain,
            provider_id=uid,
        )


class SignalHireEmailAdapter(_SignalHireHttp, EmailEnrichmentAdapter):
    """A person's work email, revealed by profile id or LinkedIn URL (one credit when found)."""

    capabilities = _capabilities(FIND_EMAIL)

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        credentials: Mapping[str, SecretStr | str] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        transport: Transport | None = None,
    ) -> None:
        EmailEnrichmentAdapter.__init__(self, config, credentials, clock=clock)
        self._setup(transport)

    @classmethod
    def validate_config(
        cls, config: Mapping[str, Any], credentials: Mapping[str, SecretStr | str] | None = None
    ) -> ConfigValidation:
        base = super().validate_config(config, credentials)
        issues = list(base.issues)
        cls._validate_base(config, issues)
        return ConfigValidation(tuple(issues))

    def test_connection(self) -> ConnectionTestResult:
        return self.timed_connection_test(self._probe_credits)

    def find_email(self, person: PersonRef, domain: str) -> AdapterResult[EmailFinding]:
        """By LinkedIn URL (``person.profile_url``); ``find_email_by_uid`` takes a SignalHire id."""
        if not person.profile_url or not LINKEDIN.fullmatch(person.profile_url):
            raise ProviderBadRequest("profile_url", provider_name=self.key)
        return self._reveal(person.profile_url)

    def find_email_by_uid(self, uid: str) -> AdapterResult[EmailFinding]:
        if not UID.fullmatch(uid or ""):
            raise ProviderBadRequest("uid", provider_name=self.key)
        return self._reveal(uid)

    def _reveal(self, item: str) -> AdapterResult[EmailFinding]:
        response = self._send(
            "POST", "/candidate/search", {"items": [item], "withoutWaterfall": True}
        )
        rows = self._json(response)
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise ProviderUpstreamError(provider_name=self.key)
        row = rows[0]
        status = row.get("status")
        provenance = self.provenance(source_ref=f"reveal:{item[:60]}")
        if status == "credits_are_over":
            raise map_http_status(402, provider_name=self.key)
        email, rating = None, None
        candidate = row.get("candidate") if status == "success" else None
        for contact in (candidate or {}).get("contacts", []) if isinstance(candidate, dict) else []:
            if isinstance(contact, dict) and contact.get("type") == "email":
                value = contact.get("value")
                if isinstance(value, str) and value.strip():
                    if email is None or (contact.get("subType") == "work"):
                        email, rating = value.strip(), contact.get("rating")
                    if contact.get("subType") == "work":
                        break
        confidence = Decimal(rating) / Decimal(100) if isinstance(rating, int) else None
        finding = EmailFinding(email=email, status="unknown", confidence=confidence)
        return AdapterResult(
            data=finding,
            provenance=provenance,
            usage=Usage(credits=Decimal(1) if status == "success" else Decimal(0)),
        )
