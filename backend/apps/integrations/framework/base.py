"""The adapter base class and the pieces every adapter shares (issue #74, ADR 0013).

An adapter is a subclass of one category base class (``operations.py``: ``CompanyDataAdapter``,
``LLMAdapter`` ...) that declares, as class attributes:

``key``           unique within its category, ``[a-z0-9][a-z0-9_.-]{0,63}``
``category``      set by the category base class
``display_name``  for the settings UI (#77)
``version``       the adapter's own version string
``terms``         ``licensed_api`` | ``public_data`` | ``manual`` (scraping is refused)
``capabilities``  ``AdapterCapabilities``: the features of its category it implements, and
                  metadata the shared layers need (allowed hosts, rate limits, paging cap)
``config_schema`` JSON-schema-like description of the settings and the credential NAMES

``test_connection()`` is the one abstract method every adapter implements. Credentials arrive as
``SecretStr`` values (``ConnectionSpec``); the framework never stores, prints or returns them.
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import Any, ClassVar, Final

from apps.core.logging import is_sensitive_key

from . import _clean
from .categories import ADAPTER_KEY_PATTERN, IntegrationCategory, check_terms, features_of
from .envelope import Provenance
from .errors import ProviderError, ProviderNotConfigured, ProviderRegistryError

_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
_JSON_TYPES: Final = {"string": str, "integer": int, "number": int | float, "boolean": bool}
_SCHEMA_KEYS: Final = frozenset(
    {"type", "enum", "minimum", "maximum", "maxLength", "default", "description", "title"}
)
EMPTY_SCHEMA: Final[Mapping[str, Any]] = MappingProxyType(
    {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
)


# ---------------------------------------------------------------------------------- secrets
class SecretStr:
    """A string that will not print itself. ``str()``, ``repr()``, f-strings and logging all show
    ``**********``; the value is only available through ``reveal()``, which adapters call at the
    moment they build a request header."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("SecretStr wraps a str")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretStr('**********')"

    __str__ = __repr__

    def __bool__(self) -> bool:
        return bool(self._value.strip())

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SecretStr) and self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)

    def __reduce__(self) -> Any:
        raise TypeError("SecretStr cannot be pickled or serialized")


def _wrap_credentials(raw: Mapping[str, SecretStr | str] | None) -> Mapping[str, SecretStr]:
    out: dict[str, SecretStr] = {}
    for name, value in (raw or {}).items():
        out[str(name)] = value if isinstance(value, SecretStr) else SecretStr(str(value))
    return MappingProxyType(out)


@dataclass(frozen=True)
class ConnectionSpec:
    """Everything the registry needs to build an adapter: which one, its non-secret settings and
    its credentials. #76 (integration settings), #77 (test connection) and #86 (jobs) construct
    it from their stored rows; this module knows nothing about those rows. ``credentials`` values
    may be ``str`` (wrapped on construction); ``repr`` never shows them."""

    category: IntegrationCategory | str
    key: str
    config: Mapping[str, Any] = field(default_factory=dict)
    credentials: Mapping[str, SecretStr | str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "category", IntegrationCategory(self.category))
        except ValueError:
            raise ProviderNotConfigured(("category",)) from None
        object.__setattr__(self, "config", MappingProxyType(dict(self.config or {})))
        object.__setattr__(self, "credentials", _wrap_credentials(self.credentials))


# ------------------------------------------------------------------------------ capabilities
@dataclass(frozen=True)
class RateLimit:
    """Vendor limits, declared so the shared limiter (#78) can enforce them. ``None`` = none."""

    per_second: int | None = None
    per_minute: int | None = None
    per_day: int | None = None


@dataclass(frozen=True)
class AdapterCapabilities:
    """What an adapter can do. ``features`` come from the closed per-category vocabulary
    (``categories.CATEGORY_FEATURES``). An adapter declares what it cannot do by leaving the
    feature out; it never fakes it (so research records "not available" instead of guessing)."""

    features: frozenset[str] = frozenset()
    requires_credentials: bool = True
    max_page_size: int = 100
    allowed_hosts: tuple[str, ...] = ()  # hosts the adapter may call (SSRF allow list, #87)
    rate_limit: RateLimit | None = None
    test_connection_costs_credits: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "features", frozenset(self.features))
        object.__setattr__(self, "allowed_hosts", tuple(self.allowed_hosts))

    def supports(self, feature: str) -> bool:
        return feature in self.features


# --------------------------------------------------------------------------- config schema
@dataclass(frozen=True)
class ConfigIssue:
    """One problem with a config. ``field`` is a setting or credential NAME, ``code`` one of
    ``required``, ``missing_credential``, ``unknown_field``, ``unknown_credential``, ``type``,
    ``enum``, ``range``, ``length``. Never a value."""

    field: str
    code: str


@dataclass(frozen=True)
class ConfigValidation:
    issues: tuple[ConfigIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(i.field for i in self.issues))

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "issues": [{"field": i.field, "code": i.code} for i in self.issues]}


def credential_names(schema: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The (required, optional) credential names a schema declares (``x-credentials``)."""
    creds = schema.get("x-credentials") or {}
    return tuple(creds.get("required", ())), tuple(creds.get("optional", ()))


def check_config_schema(schema: object) -> None:
    """Raise ``ProviderRegistryError("invalid_config_schema")`` unless ``schema`` is well formed:
    an object schema of flat properties, with credential names under ``x-credentials`` and no
    secret-looking property (secrets are credentials, never settings)."""

    def bad(why: str) -> ProviderRegistryError:
        return ProviderRegistryError("invalid_config_schema", f"config_schema: {why}.")

    if not isinstance(schema, Mapping) or schema.get("type") != "object":
        raise bad('must be an object schema (type "object")')
    props = schema.get("properties", {})
    if not isinstance(props, Mapping):
        raise bad("properties must be a mapping")
    for name, spec in props.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise bad("setting names are lower_snake_case")
        if is_sensitive_key(name):
            raise bad("secrets are credentials (x-credentials), not settings")
        if not isinstance(spec, Mapping) or spec.get("type") not in _JSON_TYPES:
            raise bad("each setting needs type string, integer, number or boolean")
        if set(spec) - _SCHEMA_KEYS:
            raise bad("unsupported keyword in a setting")
    required = schema.get("required", [])
    if not isinstance(required, list | tuple) or any(r not in props for r in required):
        raise bad("required must name declared settings")
    creds = schema.get("x-credentials", {})
    if not isinstance(creds, Mapping) or set(creds) - {"required", "optional"}:
        raise bad("x-credentials has only required and optional lists")
    names = [n for key in ("required", "optional") for n in creds.get(key, ())]
    if any(not isinstance(n, str) or not _NAME.fullmatch(n) for n in names):
        raise bad("credential names are lower_snake_case")
    if len(set(names)) != len(names) or set(names) & set(props):
        raise bad("credential names must be unique and not also settings")


def validate_against_schema(
    schema: Mapping[str, Any], config: Mapping[str, Any], credentials: Mapping[str, SecretStr]
) -> ConfigValidation:
    """Check ``config`` and ``credentials`` against ``schema``. Reports names and codes only."""
    issues: list[ConfigIssue] = []
    props: Mapping[str, Mapping[str, Any]] = schema.get("properties", {})
    for name in schema.get("required", ()):
        if config.get(name) is None and "default" not in props[name]:
            issues.append(ConfigIssue(name, "required"))
    for name, value in config.items():
        spec = props.get(name)
        if spec is None:
            issues.append(ConfigIssue(str(name), "unknown_field"))
        elif value is not None:
            code = _check_value(spec, value)
            if code:
                issues.append(ConfigIssue(name, code))
    required, optional = credential_names(schema)
    for name in required:
        if not credentials.get(name):
            issues.append(ConfigIssue(name, "missing_credential"))
    issues.extend(
        ConfigIssue(str(name), "unknown_credential")
        for name in credentials
        if name not in required and name not in optional
    )
    return ConfigValidation(tuple(issues))


def _check_value(spec: Mapping[str, Any], value: object) -> str | None:
    expected = _JSON_TYPES[spec["type"]]
    is_bool = isinstance(value, bool)
    if not isinstance(value, expected) or (is_bool and spec["type"] != "boolean"):
        return "type"
    if "enum" in spec and value not in spec["enum"]:
        return "enum"
    if (
        isinstance(value, int | float)
        and not is_bool
        and (
            ("minimum" in spec and value < spec["minimum"])
            or ("maximum" in spec and value > spec["maximum"])
        )
    ):
        return "range"
    if isinstance(value, str) and len(value) > spec.get("maxLength", 2000):
        return "length"
    return None


# ------------------------------------------------------------------------ connection test
@dataclass(frozen=True)
class ConnectionTestResult:
    """The answer to "do these settings work?" (#77). ``message`` is a fixed, safe sentence
    (never upstream text); ``error_code`` is the ``ProviderError.code`` when it failed."""

    ok: bool
    message: str
    latency_ms: int | None = None
    error_code: str | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        _clean.set_attr(self, "message", _clean.scrubbed_text(self.message, 200) or "")
        _clean.set_attr(self, "latency_ms", _clean.integer(self.latency_ms, maximum=86_400_000))
        _clean.aware(self.checked_at, "checked_at")

    @classmethod
    def success(cls, latency_ms: int | None = None) -> ConnectionTestResult:
        return cls(True, "Connection works.", latency_ms)

    @classmethod
    def failure(cls, error: ProviderError, latency_ms: int | None = None) -> ConnectionTestResult:
        return cls(False, str(error), latency_ms, error.code)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "latency_ms": self.latency_ms,
            "error_code": self.error_code,
            "checked_at": self.checked_at.isoformat(),
        }


# ---------------------------------------------------------------------------------- Adapter
class Adapter(ABC):
    """Base of every adapter. Use a category base class (``CompanyDataAdapter`` ...), not this."""

    key: ClassVar[str]
    category: ClassVar[IntegrationCategory]
    display_name: ClassVar[str]
    version: ClassVar[str] = "1.0.0"
    terms: ClassVar[str]
    capabilities: ClassVar[AdapterCapabilities]
    config_schema: ClassVar[Mapping[str, Any]] = EMPTY_SCHEMA

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        credentials: Mapping[str, SecretStr | str] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        wrapped = _wrap_credentials(credentials)
        validation = self.validate_config(config or {}, wrapped)
        if not validation.ok:
            raise ProviderNotConfigured(validation.fields, provider_name=self.key)
        props = self.config_schema.get("properties", {})
        merged = {n: s["default"] for n, s in props.items() if "default" in s}
        merged.update({k: v for k, v in (config or {}).items() if v is not None})
        self.config: Mapping[str, Any] = MappingProxyType(merged)
        self._credentials = wrapped
        self._clock = clock or (lambda: datetime.now(UTC))

    # -- construction
    @classmethod
    def validate_config(
        cls, config: Mapping[str, Any], credentials: Mapping[str, SecretStr | str] | None = None
    ) -> ConfigValidation:
        """Check settings and credential names against ``config_schema``. Override to add
        cross-field rules; return issues by field NAME and code, never values."""
        return validate_against_schema(cls.config_schema, config, _wrap_credentials(credentials))

    @classmethod
    def from_spec(cls, spec: ConnectionSpec, **kwargs: Any) -> Adapter:
        return cls(spec.config, spec.credentials, **kwargs)

    # -- what subclasses implement
    @abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        """A cheap, safe call that shows the credentials work. Return a failed result for any
        ``ProviderError``; never include credentials or upstream text in the message."""

    # -- helpers
    def credential(self, name: str) -> SecretStr:
        """A credential by name (``reveal()`` it only where the request is built)."""
        try:
            return self._credentials[name]
        except KeyError:
            raise ProviderNotConfigured((name,), provider_name=self.key) from None

    def now(self) -> datetime:
        return self._clock()

    def provenance(
        self,
        *,
        source_url: str | None = None,
        source_ref: str | None = None,
        evidence_date: date | None = None,
        request_id: str | None = None,
        cache_hit: bool = False,
        raw_ref: str | None = None,
    ) -> Provenance:
        """A ``Provenance`` stamped with this adapter's key, category, terms and clock."""
        return Provenance(
            provider=self.key,
            category=self.category,
            retrieved_at=self.now(),
            terms=self.terms,
            source_url=source_url,
            source_ref=source_ref,
            evidence_date=evidence_date,
            request_id=request_id,
            cache_hit=cache_hit,
            raw_ref=raw_ref,
        )

    def timed_connection_test(self, probe: Callable[[], object]) -> ConnectionTestResult:
        """Run ``probe`` and build the ``ConnectionTestResult`` (latency measured here)."""
        start = time.perf_counter()
        try:
            probe()
        except ProviderError as exc:
            return ConnectionTestResult.failure(exc, round((time.perf_counter() - start) * 1000))
        except Exception:
            return ConnectionTestResult.failure(
                ProviderError(provider_name=self.key), round((time.perf_counter() - start) * 1000)
            )
        return ConnectionTestResult.success(round((time.perf_counter() - start) * 1000))

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.category.value}:{self.key}>"


def check_adapter_class(cls: object) -> None:
    """Validate an adapter class's declarations (registration time). Raises
    ``ProviderRegistryError`` with a stable ``code``: ``invalid_adapter``, ``invalid_key``,
    ``invalid_category``, ``invalid_display_name``, ``terms_*`` (see ``check_terms``),
    ``invalid_capabilities``, ``invalid_config_schema``."""
    if not isinstance(cls, type) or not issubclass(cls, Adapter):
        raise ProviderRegistryError("invalid_adapter", "Register an Adapter subclass.")
    if getattr(cls, "__abstractmethods__", None):
        raise ProviderRegistryError("invalid_adapter", "The adapter has unimplemented methods.")
    key = getattr(cls, "key", None)
    if not isinstance(key, str) or not ADAPTER_KEY_PATTERN.fullmatch(key):
        raise ProviderRegistryError(
            "invalid_key", "The adapter key must be lower case letters, digits, _ . or -."
        )
    try:
        category = IntegrationCategory(str(getattr(cls, "category", None)))
    except ValueError:
        raise ProviderRegistryError(
            "invalid_category", "The adapter category is not an integration category."
        ) from None
    name = getattr(cls, "display_name", None)
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise ProviderRegistryError("invalid_display_name", "The adapter needs a display_name.")
    check_terms(getattr(cls, "terms", None))
    caps = getattr(cls, "capabilities", None)
    if not isinstance(caps, AdapterCapabilities) or not caps.features <= features_of(category):
        raise ProviderRegistryError(
            "invalid_capabilities",
            "Capabilities must be AdapterCapabilities with features of the adapter's category.",
        )
    check_config_schema(getattr(cls, "config_schema", None))


def supports(adapter: Adapter | type[Adapter] | AdapterCapabilities, feature: str) -> bool:
    """Capability negotiation: does ``adapter`` (instance, class or capabilities) implement
    ``feature``? A feature name that does not exist for the adapter's category raises
    ``ValueError`` (a typo must not read as "unsupported"). Callers branch on this instead of
    on the vendor; see ``AdapterRegistry.supporting`` for "which adapters can do X"."""
    if isinstance(adapter, AdapterCapabilities):
        return adapter.supports(feature)
    if feature not in features_of(adapter.category):
        raise ValueError(f"{feature!r} is not a feature of category {adapter.category.value!r}")
    return adapter.capabilities.supports(feature)


__all__ = [
    "EMPTY_SCHEMA",
    "Adapter",
    "AdapterCapabilities",
    "ConfigIssue",
    "ConfigValidation",
    "ConnectionSpec",
    "ConnectionTestResult",
    "RateLimit",
    "SecretStr",
    "check_adapter_class",
    "check_config_schema",
    "credential_names",
    "supports",
    "validate_against_schema",
]
