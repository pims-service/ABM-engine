"""Find the people at a company, and an approved person's email, through the client's provider.

* ``find_people_for``: searches the connected people-data provider by the campaign's preferred
  buyer titles and saves the matches as contacts. Each contact gets its own provider data source
  (``provider_reference`` holds the provider's id), so every person shows where they came from.
  Nobody is added twice (same provider id, or the same name). Ranking and the primary and
  secondary targets stay a person's call (``apply-buyer-ranking``).
* ``find_email_for``: reveals one contact's email, only for an account that was approved (ADD):
  that call can cost a credit and uses personal data, so it is never a bulk step. A found email is
  saved as ``unverified``; a miss is saved as ``not found``.

Provider failures become short messages that are safe to show. No credentials, no upstream text.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.companies import services
from apps.companies.models import Company, Contact, DataSourceType, EmailStatus
from apps.integrations.framework import CompanyRef, PersonRef, ProviderError
from apps.integrations.framework.operations import MAX_ROLES
from apps.integrations.people_connection import (
    EMAIL,
    PEOPLE,
    PeopleUnavailable,
    get_adapter,
)
from apps.outreach.decisions import ADD, latest_human_decision
from apps.research.buyers import DEFAULT_LARGE_TITLES

PROVIDER_LABEL = "SignalHire"
MAX_FOUND = 10


def _fail(error: ProviderError) -> ValidationError:
    text = str(getattr(error, "message", "") or "The provider could not answer.")
    return ValidationError(f"{PROVIDER_LABEL}: {text}")


def _known_ids(company: Company) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    names: set[str] = set()
    for contact in Contact.objects.filter(company=company).select_related("data_source"):
        names.add(contact.name.strip().lower())
        reference = contact.data_source.provider_reference
        if reference:
            ids.add(reference)
    return ids, names


def find_people_for(company: Company, user: User) -> dict[str, Any]:
    """Search and save. Returns ``{"added": [Contact], "skipped": n, "note": str}``."""
    if company.is_archived:
        raise ValidationError("Archived companies are read-only.")
    campaign = company.campaign
    try:
        adapter = get_adapter(PEOPLE, campaign.client_id, campaign=campaign, user=user)
    except PeopleUnavailable as error:
        raise ValidationError(str(error)) from None
    roles = list(campaign.current_profile.preferred_buyer_titles) or list(DEFAULT_LARGE_TITLES)
    ref = CompanyRef(name=company.name, domain=company.domain or None)
    try:
        result = adapter.find_people(ref, roles[:MAX_ROLES], limit=MAX_FOUND)
    except ProviderError as error:
        raise _fail(error) from None
    known_ids, known_names = _known_ids(company)
    added: list[Contact] = []
    skipped = 0
    for person in result.data or ():
        reference = f"{adapter.key}:{person.provider_id}" if person.provider_id else ""
        if (reference and reference in known_ids) or person.name.lower() in known_names:
            skipped += 1
            continue
        source = services.create_data_source(
            company.campaign.client,
            DataSourceType.PROVIDER,
            f"{PROVIDER_LABEL} people search",
            provider_reference=reference,
            user=user,
        )
        added.append(
            services.create_contact(
                company,
                source,
                person.name,
                title=person.title or "",
                relevance_reason=(
                    f"Listed by {PROVIDER_LABEL} as {person.title} at {company.name}."
                    if person.title
                    else f"Listed by {PROVIDER_LABEL} at {company.name}."
                ),
                user=user,
            )
        )
        known_names.add(person.name.lower())
    note = " ".join(result.warnings)
    if not added and not skipped:
        note = (note + " No matching people were found.").strip()
    return {"added": added, "skipped": skipped, "note": note}


def find_email_for(contact: Contact, user: User) -> Contact:
    """Reveal and save one contact's email (approved accounts only)."""
    company = contact.company
    if company.is_archived or contact.is_archived:
        raise ValidationError("Archived records are read-only.")
    if latest_human_decision(company) != ADD:
        raise ValidationError(
            "Emails are looked up only for approved accounts: decide ADD on this company first."
        )
    if contact.email:
        raise ValidationError("This person already has an email.")
    campaign = company.campaign
    try:
        adapter = get_adapter(EMAIL, campaign.client_id, campaign=campaign, user=user)
    except PeopleUnavailable as error:
        raise ValidationError(str(error)) from None
    reference = contact.data_source.provider_reference
    prefix = f"{adapter.key}:"
    try:
        if contact.profile_url:
            ref = PersonRef(
                contact.name, title=contact.title or None, profile_url=contact.profile_url
            )
            result = adapter.find_email(ref, company.domain or "")
        elif reference.startswith(prefix):
            result = adapter.find_email_by_uid(reference[len(prefix) :])
        else:
            raise ValidationError(
                "There is no profile link or provider id for this person; add the profile URL."
            )
    except ProviderError as error:
        raise _fail(error) from None
    email = result.data.email if result.data else None
    if email:
        return services.update_contact(contact, email=email, email_status=EmailStatus.UNVERIFIED)
    return services.update_contact(contact, email_status=EmailStatus.NOT_FOUND)
