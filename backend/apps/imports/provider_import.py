"""Provider import service (issue #67): preview, select and import companies from a provider.

Two calls, both provider-agnostic (they only know the ``CompanySearchProvider`` contract of
``apps.imports.providers`` and the registry; swapping the fake for a real adapter changes
nothing here):

``preview_provider_search(campaign, provider_name, request_or_query, *, user)``
    One page of provider results, each annotated with what an import would do. **Writes nothing**
    (no company, no batch, no data source). Statuses (``DedupeStatus``):

    * ``already_imported``  this provider company (``provider_company_key``) was imported into
      the campaign before; ``matched_company`` is the company it became;
    * ``duplicate``         a strong match (same domain or profile URL) with a company already in
      the campaign, which an import would link to, never edit;
    * ``possible_duplicate`` a weak match (same name and compatible country): an import would
      create it flagged ``possible_duplicate_of``;
    * ``new``               nothing matched;
    * ``invalid``           the data fails the shared validation (not importable).

    The default search is derived from the campaign rules summary (``from_campaign_rules``);
    ``apply_filter_overrides`` replaces filters the user edited. Provider failures raise the typed
    ``ProviderError`` subclasses (the API layer maps them to safe, stable responses).

``import_provider_companies(campaign, provider_name, *, provider_ids=... | companies=..., user)``
    Imports a selection. ``companies`` are inline ``ProviderCompany`` payloads (captured from a
    preview in the same flow); ``provider_ids`` are re-found by walking the search again (the
    contract has no get-by-id). One ``ImportBatch`` of source ``provider`` is created; its
    ``column_mapping`` holds the provenance (provider, selection mode, search request snapshot,
    ``retrieved_at``). Every company goes through the shared ``process_row`` / ``create_company``
    path, so validation, strong duplicates (never edited) and weak flags are exactly those of
    manual entry and CSV. On top of that:

    * **Idempotency**: a provider id already imported into the campaign (an earlier row with the
      same ``provider_key`` that points at a company) is a ``duplicate`` row on that company, even
      if the website changed; no second company is ever made. A strong or weak match is handled
      by ``create_company``.
    * **Provenance**: each company this import *creates* gets a ``DataSource`` (type
      ``provider``, ``name`` the provider, ``provider_reference`` the provider id, ``retrieved_at``
      from the provider result) and one ``CompanyResearch`` snapshot carrying the facts the
      provider gave (industry, description, headquarters, employee count). Duplicates are not
      edited and get no source.
    * **Size**: at most ``settings.PROVIDER_IMPORT_MAX_SELECTION`` (default 100) companies. Up to
      ``settings.PROVIDER_IMPORT_INLINE_MAX`` (default 25) run inside the request; more run in a
      Django-Q2 task tracked by a ``core.Job`` (``apps/imports/tasks.py``), and the caller gets
      the pending batch back (HTTP 202).
    * **Errors**: a provider failure while finding ids ends the batch ``partial`` (some rows
      done) or ``failed``: the rows that could not be resolved carry the stable provider error
      code (``provider_rate_limited``, ``provider_unavailable``, ``provider_quota_exceeded``...)
      and the batch ``error_summary`` the safe message. Nothing upstream is stored or logged.
    * **Audit**: one ``create`` and one ``update`` entry on the batch (client scoped).

Permission checks belong to the API layer.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.campaigns.models import Campaign
from apps.campaigns.rules_summary import build_rules_summary
from apps.companies.dedupe import find_duplicate
from apps.companies.models import Company, DataSourceType
from apps.companies.services import add_research_snapshot, create_data_source
from apps.core.audit import record_change
from apps.core.jobs import ensure_json
from apps.core.models import AuditAction

from .models import ImportBatch, ImportRow, ImportRowOutcome, ImportSource
from .providers import (
    CompanySearchProvider,
    CompanySearchRequest,
    CompanySearchResult,
    ProviderBadRequest,
    ProviderCompany,
    ProviderError,
    ProviderRegistry,
    ProviderRegistryError,
    ProviderUnavailable,
    default_registry,
    execute_search,
)
from .providers.keys import PROVIDER_ID_MAX_LENGTH
from .providers.types import DEFAULT_LIMIT, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from .schema import InputErrors
from .services import create_batch, finalize_batch, process_row, record_row_outcome

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_INLINE_MAX",
    "DEFAULT_MAX_SELECTION",
    "FILTER_OVERRIDE_KEYS",
    "PROVIDER_COMPANY_NOT_FOUND",
    "DedupeStatus",
    "PreviewItem",
    "ProviderImportResult",
    "ProviderPreview",
    "annotate_items",
    "apply_filter_overrides",
    "default_search_request",
    "describe_provider",
    "import_provider_companies",
    "inline_threshold",
    "max_selection",
    "preview_provider_search",
    "request_snapshot",
    "run_provider_import",
]

DEFAULT_MAX_SELECTION = 100
DEFAULT_INLINE_MAX = 25
#: Row error code for a selected provider id the search no longer returns.
PROVIDER_COMPANY_NOT_FOUND = "provider_company_not_found"
#: Most search pages walked to find selected provider ids (a bound, not a quota).
MAX_RESOLVE_PAGES = 20

#: Request fields a caller may override on top of the campaign-derived default.
FILTER_OVERRIDE_KEYS = (
    "countries",
    "industries",
    "employee_min",
    "employee_max",
    "business_model",
    "excluded_industries",
    "excluded_company_types",
    "sort",
)
_COMPANY_FIELDS = frozenset(f.name for f in dataclasses.fields(ProviderCompany))


def max_selection() -> int:
    """The most companies one import may select (``PROVIDER_IMPORT_MAX_SELECTION``)."""
    return max(1, int(getattr(settings, "PROVIDER_IMPORT_MAX_SELECTION", DEFAULT_MAX_SELECTION)))


def inline_threshold() -> int:
    """Selections up to this size run inside the request (``PROVIDER_IMPORT_INLINE_MAX``)."""
    return max(0, int(getattr(settings, "PROVIDER_IMPORT_INLINE_MAX", DEFAULT_INLINE_MAX)))


# ------------------------------------------------------------------ providers


def get_provider(name: str, registry: ProviderRegistry | None = None) -> CompanySearchProvider:
    """The registered provider (``ProviderRegistryError`` ``provider_not_found`` otherwise)."""
    return (registry or default_registry).get(name)


def describe_provider(provider: CompanySearchProvider) -> dict[str, Any]:
    """A provider's public description: name and capabilities. No credentials, no settings."""
    caps = provider.capabilities
    return {
        "name": provider.name,
        "terms": caps.terms,
        "supports_search": caps.supports_search,
        "supports_filters": dataclasses.asdict(caps.supports_filters),
        "max_page_size": caps.max_page_size,
        "requires_credentials": caps.requires_credentials,
    }


# ------------------------------------------------------------------ search request


def default_search_request(
    campaign: Campaign,
    *,
    query: str = "",
    page_size: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> CompanySearchRequest:
    """The search for ``campaign``: its current rules summary mapped to filters."""
    return CompanySearchRequest.from_campaign_rules(
        build_rules_summary(campaign),
        query=query,
        page_size=page_size,
        cursor=cursor,
        limit=limit,
    )


def apply_filter_overrides(
    request: CompanySearchRequest, overrides: Mapping[str, Any]
) -> CompanySearchRequest:
    """``request`` with the filters in ``overrides`` replaced (a key that is present wins, an
    empty list or ``None`` clears the filter). Unknown keys raise ``ProviderBadRequest``."""
    unknown = sorted(set(overrides) - set(FILTER_OVERRIDE_KEYS))
    if unknown:
        raise ProviderBadRequest(unknown[0])
    return dataclasses.replace(request, **dict(overrides)) if overrides else request


def request_snapshot(request: CompanySearchRequest) -> dict[str, Any]:
    """A JSON copy of a search request (the provenance stored on the batch)."""
    return {
        key: list(value) if isinstance(value, tuple) else value
        for key, value in dataclasses.asdict(request).items()
    }


def _request_from_snapshot(data: Mapping[str, Any]) -> CompanySearchRequest:
    names = {f.name for f in dataclasses.fields(CompanySearchRequest)}
    return CompanySearchRequest(**{k: v for k, v in data.items() if k in names})


# ------------------------------------------------------------------ preview


class DedupeStatus(StrEnum):
    NEW = "new"
    DUPLICATE = "duplicate"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    ALREADY_IMPORTED = "already_imported"
    INVALID = "invalid"


@dataclass(frozen=True)
class PreviewItem:
    """One search result and what importing it would do."""

    company: ProviderCompany
    provider_key: str | None
    status: DedupeStatus
    matched_company: Company | None = None
    matched_on: str | None = None
    errors: tuple[tuple[str, str, str], ...] = ()  # (field, code, message) when invalid

    @property
    def importable(self) -> bool:
        return self.status is not DedupeStatus.INVALID


@dataclass(frozen=True)
class ProviderPreview:
    """A page of annotated results plus the request that produced it."""

    provider: str
    request: CompanySearchRequest
    result: CompanySearchResult
    items: tuple[PreviewItem, ...]

    @property
    def counts(self) -> dict[str, int]:
        counts = {status.value: 0 for status in DedupeStatus}
        for item in self.items:
            counts[item.status.value] += 1
        return counts


def _prior_companies(campaign: Campaign, keys: Sequence[str]) -> dict[str, Company]:
    """``{provider_key: company}`` for provider companies already imported into the campaign
    (an earlier provider-batch row with that key that points at a company; the first one wins)."""
    if not keys:
        return {}
    rows = (
        ImportRow.objects.filter(
            batch__campaign=campaign,
            batch__source=ImportSource.PROVIDER,
            company__isnull=False,
            raw_data__provider_key__in=list(keys),
        )
        .select_related("company")
        .order_by("created_at", "row_number")
    )
    found: dict[str, Company] = {}
    for row in rows:
        key = row.raw_data.get("provider_key")
        if isinstance(key, str) and row.company is not None:
            found.setdefault(key, row.company)
    return found


def annotate_items(
    campaign: Campaign, provider_name: str, companies: Sequence[ProviderCompany]
) -> tuple[PreviewItem, ...]:
    """Dedupe status of each company against the campaign. Read-only (``find_duplicate`` and one
    key lookup); order and length are kept."""
    keys = [c.provider_key(provider_name) for c in companies]
    prior = _prior_companies(campaign, [k for k in keys if k])
    items: list[PreviewItem] = []
    for company, key in zip(companies, keys, strict=True):
        checked = company.to_company_input()
        if isinstance(checked, InputErrors):
            errors = tuple((e.field, e.code, e.message) for e in checked.errors)
            items.append(PreviewItem(company, key, DedupeStatus.INVALID, errors=errors))
            continue
        if key and key in prior:
            items.append(
                PreviewItem(
                    company,
                    key,
                    DedupeStatus.ALREADY_IMPORTED,
                    matched_company=prior[key],
                    matched_on="provider_key",
                )
            )
            continue
        match = find_duplicate(
            campaign, checked.name, checked.website, checked.profile_url, checked.country
        )
        if match.is_strong:
            status = DedupeStatus.DUPLICATE
        elif match.is_weak:
            status = DedupeStatus.POSSIBLE_DUPLICATE
        else:
            status = DedupeStatus.NEW
        items.append(
            PreviewItem(
                company,
                key,
                status,
                matched_company=match.company,
                matched_on=match.matched_on,
            )
        )
    return tuple(items)


def preview_provider_search(
    campaign: Campaign,
    provider_name: str,
    request: CompanySearchRequest | str | None = None,
    *,
    user: User | None = None,
    registry: ProviderRegistry | None = None,
) -> ProviderPreview:
    """One page of ``provider_name`` results for ``campaign``, annotated; writes nothing.

    ``request`` is a ready ``CompanySearchRequest`` (paging and filters decided by the caller),
    a query string (the campaign-derived default with that free text) or ``None`` (the default).
    Raises ``ProviderRegistryError`` for an unknown provider and ``ProviderError`` subclasses
    for provider failures; the original upstream error is never copied into either.
    """
    provider = get_provider(provider_name, registry)
    if not isinstance(request, CompanySearchRequest):
        request = default_search_request(campaign, query=request or "")
    try:
        result = execute_search(provider, request)
    except ProviderError as exc:
        exc.provider_name = exc.provider_name or provider.name
        logger.warning(
            "Provider preview failed: provider=%s code=%s user=%s campaign=%s",
            provider.name,
            exc.code,
            getattr(user, "pk", None),
            campaign.pk,
        )
        raise
    items = annotate_items(campaign, provider.name, result.items)
    logger.info(
        "Provider preview: provider=%s results=%d user=%s campaign=%s",
        provider.name,
        len(items),
        getattr(user, "pk", None),
        campaign.pk,
    )
    return ProviderPreview(provider.name, request, result, items)


# ------------------------------------------------------------------ import


@dataclass
class ProviderImportResult:
    """What ``import_provider_companies`` did. ``background`` is True when the rows are processed
    by a job (the batch may still be pending or processing)."""

    batch: ImportBatch
    background: bool
    provider_error: dict[str, object] | None = field(default=None)


def _company_from(value: ProviderCompany | Mapping[str, Any]) -> ProviderCompany:
    if isinstance(value, ProviderCompany):
        return value
    try:
        return ProviderCompany(**{k: v for k, v in value.items() if k in _COMPANY_FIELDS})
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError({"companies": "A selected company is not valid."}) from exc


def _clean_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > PROVIDER_ID_MAX_LENGTH
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValidationError({"provider_ids": "A selected provider id is not valid."})
    return value.strip()


def _entries(
    provider_name: str,
    provider_ids: Sequence[str] | None,
    companies: Sequence[ProviderCompany | Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """The selection as JSON entries (``{"provider_id": ...}`` or ``{"company": {...}}``), checked
    against the size limit, de-duplicated by provider id."""
    if bool(provider_ids) == bool(companies):
        raise ValidationError(
            {"selection": "Select companies with either provider_ids or companies, not both."}
        )
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    if provider_ids:
        for value in provider_ids:
            pid = _clean_id(value)
            if pid not in seen:
                seen.add(pid)
                entries.append({"provider_id": pid})
    else:
        for item in companies or ():
            company = _company_from(item)
            key = company.provider_key(provider_name)
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            entries.append({"company": dataclasses.asdict(company)})
    limit = max_selection()
    if len(entries) > limit:
        raise ValidationError(
            {"selection": f"Select at most {limit} companies per import (got {len(entries)})."}
        )
    return entries


def import_provider_companies(
    campaign: Campaign,
    provider_name: str,
    *,
    provider_ids: Sequence[str] | None = None,
    companies: Sequence[ProviderCompany | Mapping[str, Any]] | None = None,
    search: CompanySearchRequest | None = None,
    retrieved_at: datetime | None = None,
    user: User | None = None,
    registry: ProviderRegistry | None = None,
) -> ProviderImportResult:
    """Import the selected provider companies into ``campaign`` (see the module doc).

    Pass exactly one of ``provider_ids`` and ``companies``. ``search`` is the request the
    selection came from: stored as provenance and, for ``provider_ids``, what is searched to find
    them (default: the campaign-derived search). ``retrieved_at`` is when the inline companies
    were retrieved (default now); ids take the time of the provider result that returned them.

    Raises Django ``ValidationError`` for an archived campaign or client or a bad or oversized
    selection, and ``ProviderRegistryError`` for an unknown provider. Provider failures while
    finding ids do not raise: they end the batch ``partial`` or ``failed``.
    """
    provider = get_provider(provider_name, registry)  # the registry holds the canonical name
    if campaign.is_archived:
        raise ValidationError("Cannot import into an archived campaign.")
    if campaign.client.is_archived:
        raise ValidationError("Cannot import for an archived client.")
    entries = _entries(provider.name, provider_ids, companies)
    when = retrieved_at or timezone.now()
    mode = "ids" if provider_ids else "inline"
    if mode == "ids" and search is None:
        search = default_search_request(campaign)
    provenance: dict[str, Any] = {
        "provider": provider.name,
        "terms": provider.capabilities.terms,
        "selection": mode,
        "requested_count": len(entries),
        "retrieved_at": when.isoformat(),
        "search_request": request_snapshot(search) if search is not None else None,
    }
    batch = create_batch(
        campaign,
        ImportSource.PROVIDER,
        user=user,
        column_mapping=provenance,
        total_count=len(entries),
    )
    record_change(
        AuditAction.CREATE,
        "import_batch",
        batch.pk,
        actor=user,
        client=campaign.client_id,
        after={
            "campaign_id": str(campaign.pk),
            "source": batch.source,
            "provider": provider.name,
            "selection": mode,
            "total_count": batch.total_count,
        },
    )
    if len(entries) <= inline_threshold():
        try:
            outcome = run_provider_import(batch, entries, user=user, registry=registry)
        except Exception:
            _fail_unfinished(batch)
            raise
        return ProviderImportResult(
            batch=outcome.batch, background=False, provider_error=outcome.provider_error
        )

    from apps.core.jobs import enqueue

    from .tasks import import_provider_batch

    try:
        ensure_json(entries, "Selection")
        enqueue(
            import_provider_batch,
            client=campaign.client,
            campaign=campaign,
            created_by=user,
            batch_id=str(batch.pk),
            entries=entries,
            user_id=str(user.pk) if user is not None else None,
        )
    except Exception:
        _fail_unfinished(batch)
        raise
    batch.refresh_from_db()
    return ProviderImportResult(batch=batch, background=True)


def _fail_unfinished(batch: ImportBatch) -> None:
    """The run broke as a whole: end the batch ``failed`` (never leave it processing)."""
    from .services import fail_batch

    batch.refresh_from_db()
    if not batch.is_finished:
        fail_batch(batch, "The import could not be completed. Please try again.")
        _audit_finished(batch)


def _audit_finished(batch: ImportBatch) -> None:
    record_change(
        AuditAction.UPDATE,
        "import_batch",
        batch.pk,
        actor=batch.created_by,
        client=batch.client_id,
        before={"status": "pending"},
        after={
            "status": batch.status,
            "total_count": batch.total_count,
            "created_count": batch.created_count,
            "duplicate_count": batch.duplicate_count,
            "restored_count": batch.restored_count,
            "failed_count": batch.failed_count,
        },
    )


# ------------------------------------------------------------------ the run (inline or job)


def _resolve_ids(
    provider: CompanySearchProvider, request: CompanySearchRequest, ids: Sequence[str]
) -> tuple[dict[str, tuple[ProviderCompany, datetime]], ProviderError | None]:
    """Find the selected ``ids`` by walking the search pages (bounded). Returns what was found
    (with the retrieval time of its page) and the provider error that stopped the walk, if any."""
    wanted = set(ids)
    found: dict[str, tuple[ProviderCompany, datetime]] = {}
    walk = dataclasses.replace(request, page_size=MAX_PAGE_SIZE, cursor=None)
    for _ in range(MAX_RESOLVE_PAGES):
        try:
            page = execute_search(provider, walk)
        except ProviderError as exc:
            exc.provider_name = exc.provider_name or provider.name
            logger.warning(
                "Provider id lookup failed: provider=%s code=%s", provider.name, exc.code
            )
            return found, exc
        for company in page.items:
            if company.provider_id in wanted and company.provider_id not in found:
                found[company.provider_id] = (company, page.retrieved_at)
        if len(found) == len(wanted) or page.next_cursor is None:
            break
        walk = walk.with_cursor(page.next_cursor)
    return found, None


def _row_raw(company: ProviderCompany, provider_name: str) -> dict[str, Any]:
    """The row payload: all provider fields, with the website falling back to the domain hint so
    the shared validation sees what ``ProviderCompany.to_company_input`` sees."""
    return {
        **company.to_raw_data(provider_name),
        "website": company.website or company.domain_hint,
    }


def _prior_company(campaign: Campaign, key: str | None) -> Company | None:
    return _prior_companies(campaign, [key]).get(key) if key else None


def _attach_provenance(
    company: Company,
    provider_name: str,
    item: ProviderCompany,
    retrieved_at: datetime,
    user: User | None,
) -> None:
    """The ``DataSource`` and first ``CompanyResearch`` snapshot of a company this import made.
    Idempotent: a company that already has a provider snapshot is left alone."""
    if company.research.filter(
        data_source__type=DataSourceType.PROVIDER, data_source__name=provider_name
    ).exists():
        return
    with transaction.atomic():
        try:
            source = create_data_source(
                company.client,
                DataSourceType.PROVIDER,
                provider_name,
                url=item.provider_url,
                provider_reference=item.provider_id,
                retrieved_at=retrieved_at,
                user=user,
            )
        except ValidationError:  # a record URL that is not a valid URL: keep the rest
            source = create_data_source(
                company.client,
                DataSourceType.PROVIDER,
                provider_name,
                provider_reference=item.provider_id,
                retrieved_at=retrieved_at,
                user=user,
            )
        facts = {
            "industry": item.industry or None,
            "description": item.description or None,
            "headquarters": item.headquarters or None,
            "employee_count": item.employee_count,
        }
        add_research_snapshot(company, source, researched_at=retrieved_at, **facts)


def _import_one(
    batch: ImportBatch,
    row_number: int,
    item: ProviderCompany,
    retrieved_at: datetime,
    user: User | None,
) -> None:
    provider_name = batch.column_mapping["provider"]
    raw = _row_raw(item, provider_name)
    key = item.provider_key(provider_name)
    checked = item.to_company_input()
    prior = None if isinstance(checked, InputErrors) else _prior_company(batch.campaign, key)
    if prior is not None and not isinstance(checked, InputErrors):
        # The same provider company as an earlier import: never a second company.
        with transaction.atomic():
            restored = prior.is_archived
            if restored:
                prior.restore()
            record_row_outcome(
                batch,
                row_number,
                ImportRowOutcome.RESTORED if restored else ImportRowOutcome.DUPLICATE,
                raw=raw,
                company_input=checked,
                company=prior,
            )
        return
    row = process_row(batch, row_number, raw, user=user)
    if row.outcome == ImportRowOutcome.CREATED and row.company is not None:
        _attach_provenance(row.company, provider_name, item, retrieved_at, user)


def run_provider_import(
    batch: ImportBatch,
    entries: Sequence[Mapping[str, Any]],
    *,
    user: User | None,
    registry: ProviderRegistry | None = None,
) -> ProviderImportResult:
    """Process every entry of ``entries`` (row numbers 1..n in order) into ``batch`` and finalize
    it. Idempotent per row: a redelivered run skips the rows already recorded. Called inline by
    ``import_provider_companies`` and by the background task."""
    provenance = batch.column_mapping
    provider_name = str(provenance["provider"])
    when = datetime.fromisoformat(str(provenance["retrieved_at"]))
    done = set(batch.rows.values_list("row_number", flat=True))
    pending_ids = [
        str(e["provider_id"])
        for number, e in enumerate(entries, start=1)
        if "provider_id" in e and number not in done
    ]

    error: ProviderError | None = None
    resolved: dict[str, tuple[ProviderCompany, datetime]] = {}
    if pending_ids:
        try:
            provider = get_provider(provider_name, registry)
        except ProviderRegistryError:
            error = ProviderUnavailable(provider_name=provider_name)
        else:
            snapshot = provenance.get("search_request")
            request = (
                _request_from_snapshot(snapshot)
                if isinstance(snapshot, Mapping)
                else default_search_request(batch.campaign)
            )
            resolved, error = _resolve_ids(provider, request, pending_ids)

    for number, entry in enumerate(entries, start=1):
        if number in done:
            continue
        if "company" in entry:
            _import_one(batch, number, _company_from(entry["company"]), when, user)
            continue
        pid = str(entry["provider_id"])
        found = resolved.get(pid)
        if found is None:
            record_row_outcome(
                batch,
                number,
                ImportRowOutcome.FAILED,
                raw={"provider": provider_name, "provider_id": pid},
                error_code=error.code if error else PROVIDER_COMPANY_NOT_FOUND,
                error_message=(
                    error.message
                    if error
                    else "The company is not in the provider results for this search."
                ),
            )
            continue
        _import_one(batch, number, found[0], found[1], user)

    finalize_batch(batch, error_summary=error.message if error else "")
    batch.refresh_from_db()
    _audit_finished(batch)
    return ProviderImportResult(
        batch=batch, background=False, provider_error=error.as_dict() if error else None
    )
