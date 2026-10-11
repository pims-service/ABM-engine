"""Write path for companies, research snapshots and data sources.

Permission checks belong to the API layer (issue #46); these functions enforce data rules.

* ``create_company``: merge-safe upsert. A strong match (domain, then canonical profile URL)
  links to the existing company without editing it and restores it if archived; a weak match
  (name and country) creates the company flagged ``possible_duplicate_of`` the candidate.
  Race-safe through the unique indexes.
* ``add_research_snapshot``: append a new ``CompanyResearch`` row. Never edits an old one.
* ``create_data_source``: record one retrieval of evidence for a client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.models import User
from apps.campaigns.models import Campaign, Client

from .dedupe import NO_MATCH, DuplicateMatch, find_duplicate
from .domain import normalize_domain
from .models import (
    Company,
    CompanyResearch,
    Contact,
    ContactRole,
    DataSource,
    DataSourceType,
    EmailStatus,
    InputSource,
)
from .normalize import profile_key

__all__ = [
    "CompanyResult",
    "DuplicateMatch",
    "add_research_snapshot",
    "create_company",
    "create_contact",
    "create_data_source",
    "find_duplicate",
    "normalize_domain",
    "restore_contact",
    "set_contact_role",
    "update_contact",
]

RESEARCH_FIELDS = (
    "industry",
    "description",
    "headquarters",
    "employee_count",
    "business_model",
    "classification",
    "products_services",
    "target_customers",
    "sales_headcount",
    "bd_headcount",
    "marketing_headcount",
    "commercial_partnerships_headcount",
    "department_growth",
    "headcount_change_3m",
    "headcount_change_6m",
    "headcount_change_12m",
)


@dataclass
class CompanyResult:
    """Outcome of ``create_company``.

    ``created`` is True for a new row. A strong duplicate (same normalized domain or canonical
    profile URL in the campaign) returns the existing company, untouched, with ``duplicate``
    True and ``restored`` True when it had been archived. ``match`` is what
    ``dedupe.find_duplicate`` found (``strength`` strong, weak or none, ``matched_on``,
    ``candidates``). On a weak match the company is created and flagged
    (``company.possible_duplicate_of`` is the candidate); ``similar`` lists the candidates (a
    warning for a person to decide on; nothing is merged automatically).
    """

    company: Company
    created: bool = False
    duplicate: bool = False
    restored: bool = False
    similar: list[Company] = field(default_factory=list)
    match: DuplicateMatch = NO_MATCH

    @property
    def possible_duplicate(self) -> bool:
        return self.created and self.match.is_weak

    @property
    def warnings(self) -> list[str]:
        if self.similar:
            return [f"A company named '{self.company.name}' already exists in this campaign."]
        return []


def create_data_source(
    client: Client,
    type: str,
    name: str,
    *,
    url: str = "",
    provider_reference: str = "",
    retrieved_at: datetime | None = None,
    evidence_date: Any = None,
    user: User | None = None,
) -> DataSource:
    """Insert one ``DataSource`` (append-only). ``retrieved_at`` defaults to now."""
    extra: dict[str, Any] = {} if retrieved_at is None else {"retrieved_at": retrieved_at}
    source = DataSource(
        client=client,
        type=type,
        name=name.strip(),
        url=url.strip(),
        provider_reference=provider_reference.strip(),
        evidence_date=evidence_date,
        created_by=user,
        **extra,
    )
    source.full_clean(exclude=["client"], validate_constraints=False)
    if type not in (DataSourceType.MANUAL, DataSourceType.PROVIDER) and not source.url:
        raise ValidationError({"url": "A URL is required unless the source is manual or provider."})
    source.save()
    return source


def _duplicate_result(match: DuplicateMatch) -> CompanyResult:
    company = match.company
    assert company is not None  # noqa: S101 - a strong match always has a company
    restored = company.is_archived
    if restored:
        company.restore()
    return CompanyResult(company=company, duplicate=True, restored=restored, match=match)


def create_company(
    campaign: Campaign,
    name: str,
    website: str = "",
    *,
    profile_url: str = "",
    country: str = "",
    input_source: str = InputSource.MANUAL,
    user: User | None = None,
    created_by_job_id: Any = None,
) -> CompanyResult:
    """Add a company to a campaign, or report the one that is already there (merge-safe).

    Matching is ``dedupe.find_duplicate`` (domain, then canonical profile URL, then name and
    country), inside this campaign only.

    * Strong match (same normalized domain or profile URL, archived or not): nothing is
      inserted and the existing company is **never edited**: none of the submitted values
      (name, website, country...) is copied onto it. It is returned with ``duplicate=True``
      and, if it was archived, restored (``restored=True``, the one change allowed). The
      database also forbids a second row with the same domain or profile key.
    * Weak match (same name key, compatible country, no conflicting domain): the company is
      created with ``possible_duplicate_of`` pointing at the oldest candidate, set once and
      never edited; ``similar`` lists the candidates. Nothing is merged.
    * Race: if another writer inserts the same domain or profile between our lookup and our
      insert, the unique index raises ``IntegrityError``; we look again and return the winner
      as a duplicate. Exactly one company results.
    * An archived campaign takes no new companies. A website that has no valid domain name is
      a ``ValidationError``.
    """
    name = name.strip()
    website = website.strip()
    if not name:
        raise ValidationError({"name": "A company needs a name."})
    if campaign.is_archived:
        raise ValidationError("Cannot add a company to an archived campaign.")
    domain = normalize_domain(website)
    if website and domain is None:
        raise ValidationError({"website": "Enter a website with a valid domain name."})

    profile_url = profile_url.strip()
    country = country.strip().upper()

    match = find_duplicate(campaign, name, website, profile_url, country)
    if match.is_strong:
        return _duplicate_result(match)

    company = Company(
        campaign=campaign,
        name=name,
        website=website,
        profile_url=profile_url,
        country=country,
        input_source=input_source,
        created_by=user,
        created_by_job_id=created_by_job_id,
        possible_duplicate_of=match.company if match.is_weak else None,
    )
    company.full_clean(
        exclude=["client", "campaign"], validate_unique=False, validate_constraints=False
    )
    try:
        with transaction.atomic():  # savepoint: a race with another writer must not break ours
            company.save()
    except IntegrityError:
        if domain is None and not profile_key(profile_url):
            raise
        winner = find_duplicate(campaign, name, website, profile_url, country)
        if not winner.is_strong:
            raise
        return _duplicate_result(winner)

    return CompanyResult(
        company=company,
        created=True,
        similar=list(match.candidates) if match.is_weak else [],
        match=match,
    )


@transaction.atomic
def add_research_snapshot(
    company: Company,
    data_source: DataSource,
    *,
    researched_at: datetime | None = None,
    **facts: Any,
) -> CompanyResearch:
    """Append a research snapshot (history, Brief section 19). Old snapshots are untouched.

    ``facts`` are any of ``RESEARCH_FIELDS``; omitted facts stay null ("not found"). The data
    source must belong to the same client as the company. ``researched_at`` is when the data
    was retrieved (defaults to the source's ``retrieved_at``) and decides which snapshot is
    current, so back-filling an older snapshot does not replace a newer one.
    """
    unknown = sorted(set(facts) - set(RESEARCH_FIELDS))
    if unknown:
        raise ValidationError(dict.fromkeys(unknown, "Unknown field."))
    if company.is_archived:
        raise ValidationError("Archived companies are read-only.")
    if data_source.client_id != company.client_id:
        raise ValidationError({"data_source": "The data source belongs to a different client."})

    snapshot = CompanyResearch(
        company=company,
        data_source=data_source,
        researched_at=researched_at or data_source.retrieved_at,
        **facts,
    )
    snapshot.full_clean(
        exclude=["client", "company", "data_source"],
        validate_unique=False,
        validate_constraints=False,
    )
    snapshot.save()
    return snapshot


# ------------------------------------------------------------------ contacts (issue #41)

CONTACT_FIELDS = (
    "name",
    "title",
    "profile_url",
    "email",
    "email_status",
    "relevance_reason",
    "rank",
)


def _validate_contact(contact: Contact) -> None:
    contact.full_clean(
        exclude=["client", "company", "data_source"],
        validate_unique=False,
        validate_constraints=False,
    )


def _free_slot(company: Company, role: str, *, except_pk: Any = None) -> None:
    """Demote the non-archived holder of a primary/secondary slot to ``none``."""
    if role not in (ContactRole.PRIMARY, ContactRole.SECONDARY):
        return
    holders = Contact.objects.active().filter(company=company, role=role)
    if except_pk is not None:
        holders = holders.exclude(pk=except_pk)
    for holder in holders:
        holder.role = ContactRole.NONE
        holder.save(update_fields=["role", "updated_at"])


@transaction.atomic
def create_contact(
    company: Company,
    data_source: DataSource,
    name: str,
    *,
    title: str = "",
    profile_url: str = "",
    email: str = "",
    email_status: str | None = None,
    relevance_reason: str = "",
    rank: int | None = None,
    role: str = ContactRole.NONE,
    user: User | None = None,
) -> Contact:
    """Add a contact. Taking the primary or secondary slot demotes the previous holder to none."""
    if company.is_archived:
        raise ValidationError("Archived companies are read-only.")
    if data_source.client_id != company.client_id:
        raise ValidationError({"data_source": "The data source belongs to a different client."})
    email = email.strip()
    if email_status is None:
        email_status = EmailStatus.UNVERIFIED if email else EmailStatus.UNKNOWN
    contact = Contact(
        company=company,
        data_source=data_source,
        name=name.strip(),
        title=title.strip(),
        profile_url=profile_url.strip(),
        email=email,
        email_status=email_status,
        relevance_reason=relevance_reason.strip(),
        rank=rank,
        role=role,
        created_by=user,
    )
    _validate_contact(contact)
    _check_contact_rules(contact)
    _free_slot(company, role)
    contact.save()
    return contact


def _check_contact_rules(contact: Contact) -> None:
    """Friendly errors for rules the database also enforces."""
    if contact.email_status == EmailStatus.NOT_FOUND and contact.email:
        raise ValidationError({"email_status": "'not found' cannot have an email."})
    if not contact.email and contact.email_status not in (
        EmailStatus.UNKNOWN,
        EmailStatus.NOT_FOUND,
    ):
        raise ValidationError({"email_status": "This status needs an email address."})
    if contact.profile_url and contact.archived_at is None:
        clash = Contact.objects.active().filter(
            company=contact.company, profile_url=contact.profile_url
        )
        if contact.pk:
            clash = clash.exclude(pk=contact.pk)
        if clash.exists():
            raise ValidationError({"profile_url": "This person is already a contact."})


@transaction.atomic
def update_contact(contact: Contact, **changes: Any) -> Contact:
    """Edit enrichment fields (``CONTACT_FIELDS``). Use ``set_contact_role`` for the role."""
    unknown = sorted(set(changes) - set(CONTACT_FIELDS))
    if unknown:
        raise ValidationError(dict.fromkeys(unknown, "Unknown or read-only field."))
    if contact.is_archived:
        raise ValidationError("Archived contacts are read-only.")
    for key, value in changes.items():
        setattr(contact, key, value.strip() if isinstance(value, str) else value)
    _validate_contact(contact)
    _check_contact_rules(contact)
    contact.save()
    return contact


@transaction.atomic
def set_contact_role(contact: Contact, role: str) -> Contact:
    """Give a contact a role. Taking a taken slot demotes the old holder to none."""
    if role not in ContactRole.values:
        raise ValidationError({"role": "Role must be primary, secondary or none."})
    if contact.is_archived:
        raise ValidationError("Archived contacts are read-only.")
    _free_slot(contact.company, role, except_pk=contact.pk)
    contact.role = role
    contact.save(update_fields=["role", "updated_at"])
    return contact


@transaction.atomic
def restore_contact(contact: Contact) -> Contact:
    """Un-archive. If its primary/secondary slot was taken meanwhile it returns as ``none``."""
    if contact.erased_at is not None:
        raise ValidationError("An erased contact cannot be restored.")
    if contact.is_archived and contact.role != ContactRole.NONE:
        taken = (
            Contact.objects.active()
            .filter(company=contact.company, role=contact.role)
            .exclude(pk=contact.pk)
            .exists()
        )
        if taken:
            contact.role = ContactRole.NONE
            contact.save(update_fields=["role", "updated_at"])
    contact.restore()
    return contact
