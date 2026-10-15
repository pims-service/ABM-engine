"""GoHighLevel (HighLevel) CRM adapter: push an approved contact with a tag and a note (#85).

Checked against HighLevel's own API reference on 2026-10-15 (API version ``2021-07-28``):

* Base URL ``https://services.leadconnectorhq.com`` and ``Authorization: Bearer <private
  integration token>`` plus ``Version: 2021-07-28``
  (https://marketplace.gohighlevel.com/docs/Authorization/PrivateIntegrationsToken).
* Upsert: ``POST /contacts/upsert`` with ``locationId`` (required), ``name``, ``email``,
  ``companyName``, ``source``; answers ``{"new": bool, "contact": {"id": ...}}``. Whether an
  existing contact is matched follows the location's "Allow Duplicate Contact" setting
  (https://marketplace.gohighlevel.com/docs/2021-07-28/ghl/contacts/upsert-contact). ``tags`` on
  the upsert REPLACES every tag, so tags are added separately.
* Add tags: ``POST /contacts/{contactId}/tags`` with ``{"tags": [...]}`` (adds, never replaces).
* Add a note: ``POST /contacts/{contactId}/notes`` with ``{"body": "..."}``.
* The connection test reads the location: ``GET /locations/{locationId}``.

NOT verified against a live account (no token available here): the private integration needs the
contact write scopes (and location read for the test), and whether the location accepts a contact
with an email only. Idempotency: the same email updates the same contact (the location's duplicate
setting decides), and the note is added only when the contact was just created, so pushing again
never piles up notes.

The transport is the same injectable, host-allow-listed, TLS-verifying one the Anthropic adapter
uses; tests never touch the network.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, ClassVar
from urllib.parse import quote, urlsplit

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
    CRMAdapter,
    CRMContact,
    CRMPushResult,
    ProviderUpstreamError,
    SecretStr,
    map_http_status,
)
from apps.integrations.framework.categories import PUSH_CONTACT

API_HOST = "services.leadconnectorhq.com"
DEFAULT_API_BASE = f"https://{API_HOST}"
API_VERSION = "2021-07-28"
_NOTE_KEY = re.compile(r"note_(\d+)")


class GoHighLevelAdapter(CRMAdapter):
    """Push contacts into one HighLevel location."""

    key = "gohighlevel"
    display_name = "GoHighLevel"
    version = "1.0.0"
    terms = "licensed_api"
    capabilities = AdapterCapabilities(
        features=frozenset({PUSH_CONTACT}),
        requires_credentials=True,
        allowed_hosts=(API_HOST,),
        test_connection_costs_credits=False,
    )
    config_schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "location_id": {
                "type": "string",
                "maxLength": 100,
                "description": "The HighLevel location (sub-account) id contacts go into.",
            },
            "api_base": {
                "type": "string",
                "default": DEFAULT_API_BASE,
                "maxLength": 200,
                "description": "HTTPS base URL; the host must be on the allow list.",
            },
            "tag": {
                "type": "string",
                "default": "ABM Engine",
                "maxLength": 60,
                "description": "Tag added to every pushed contact.",
            },
            "source": {
                "type": "string",
                "default": "ABM Engine",
                "maxLength": 60,
                "description": "Source shown on contacts this product creates.",
            },
            "timeout_seconds": {
                "type": "number",
                "default": 30,
                "minimum": 1,
                "maximum": 120,
                "description": "Connect and read timeout per request.",
            },
        },
        "required": ["location_id"],
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
        location = config.get("location_id")
        if (
            isinstance(location, str)
            and not re.fullmatch(r"[A-Za-z0-9]{4,100}", location.strip())
            and not any(i.field == "location_id" for i in issues)
        ):
            issues.append(ConfigIssue("location_id", "pattern"))
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
    def _send(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> HttpResponse:
        headers = {
            "Authorization": f"Bearer {self.credential('api_key').reveal()}",
            "Version": API_VERSION,
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
                provider_name=self.key,
                retry_after=response.headers.get("retry-after"),
            )
        return response

    def _json(self, response: HttpResponse) -> dict[str, Any]:
        try:
            decoded = json.loads(response.body)
        except ValueError:
            raise ProviderUpstreamError(provider_name=self.key) from None
        if not isinstance(decoded, dict):
            raise ProviderUpstreamError(provider_name=self.key)
        return decoded

    # ------------------------------------------------------------------ operations
    def test_connection(self) -> ConnectionTestResult:
        """``GET /locations/{id}``: shows the token and the location id fit together."""
        location = quote(str(self.config["location_id"]), safe="")

        def probe() -> None:
            if not isinstance(self._json(self._send("GET", f"/locations/{location}")), dict):
                raise ProviderUpstreamError(provider_name=self.key)

        return self.timed_connection_test(probe)

    def push_contact(self, contact: CRMContact) -> AdapterResult[CRMPushResult]:
        fields = contact.fields
        body: dict[str, Any] = {
            "locationId": str(self.config["location_id"]),
            "name": contact.name,
            "source": str(self.config["source"]),
        }
        if contact.email:
            body["email"] = contact.email
        company = fields.get("company_name")
        if company:
            body["companyName"] = company
        response = self._send("POST", "/contacts/upsert", body)
        data = self._json(response)
        record = data.get("contact")
        remote_id = record.get("id") if isinstance(record, dict) else None
        if not isinstance(remote_id, str) or not remote_id:
            raise ProviderUpstreamError(provider_name=self.key)
        created = data.get("new") is True
        path = quote(remote_id, safe="")
        self._send("POST", f"/contacts/{path}/tags", {"tags": [str(self.config["tag"])]})
        note = self._note(contact)
        if note and (created or fields.get("refresh_note") == "1"):
            self._send("POST", f"/contacts/{path}/notes", {"body": note})
        return AdapterResult(
            data=CRMPushResult(
                contact.external_key, remote_id, "created" if created else "updated"
            ),
            provenance=self.provenance(
                source_ref=f"contact:{remote_id}", request_id=response.headers.get("traceid")
            ),
        )

    @staticmethod
    def _note(contact: CRMContact) -> str:
        """The note text: title, then ``note_1``, ``note_2`` ... in order (each part is short
        because CRM fields are length limited)."""
        parts = sorted(
            (int(m.group(1)), value)
            for key, value in contact.fields.items()
            if (m := _NOTE_KEY.fullmatch(key))
        )
        lines = [f"Title: {contact.title}"] if contact.title else []
        lines += [text for _, text in parts]
        return "\n".join(lines).strip()
