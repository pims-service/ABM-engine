"""Integration categories, access terms and the capability vocabulary (issue #74, ADR 0013).

The nine categories are the Brief section 17 list. Each category has a closed set of feature names
an adapter may declare (``CATEGORY_FEATURES``); ``supports`` answers "can this adapter do X?"
without the caller knowing the vendor. Declaring something outside the set is refused at
registration, so a typo or an out-of-scope feature (outreach sending in V1) cannot slip in.
"""

from __future__ import annotations

import re
from enum import StrEnum

from .errors import ProviderRegistryError


class IntegrationCategory(StrEnum):
    """Brief section 17: what kinds of external service the product talks to."""

    LLM = "llm"
    COMPANY_DATA = "company_data"
    PEOPLE_DATA = "people_data"
    EMAIL_ENRICHMENT = "email_enrichment"
    EMAIL_VERIFICATION = "email_verification"
    CRM = "crm"
    OUTREACH = "outreach"
    STORAGE = "storage"
    AUTOMATION = "automation"


# ------------------------------------------------------------------------------------ terms
ALLOWED_TERMS: frozenset[str] = frozenset({"licensed_api", "public_data", "manual"})
_FORBIDDEN_MARKERS = ("scrap", "crawl", "spider", "harvest", "unauthorized", "unlicensed")

#: Adapter and provider keys: lower case, no ``:`` (so ``provider:id`` keys stay unambiguous).
ADAPTER_KEY_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")


def check_terms(terms: object) -> None:
    """Raise ``ProviderRegistryError`` unless ``terms`` is an allowed access basis.

    ``licensed_api`` (an API we hold a licence or key for), ``public_data`` (an open dataset or
    public API whose terms allow this use) or ``manual`` (people type or upload data). Scraping
    and unknown terms are refused (Brief section 4, ADR 0002, ADR 0011).
    """
    if not isinstance(terms, str) or not terms.strip():
        raise ProviderRegistryError("terms_missing", "The provider must declare its terms.")
    value = terms.strip().lower()
    if any(marker in value for marker in _FORBIDDEN_MARKERS):
        raise ProviderRegistryError(
            "terms_forbidden",
            "Scraping-style access is not allowed. "
            "Use a licensed API, public data or manual input.",
        )
    if value not in ALLOWED_TERMS:
        raise ProviderRegistryError(
            "terms_unknown", "The provider terms must be licensed_api, public_data or manual."
        )


# -------------------------------------------------------------------------------- features
# Company data (#82)
SEARCH = "search"
ENRICH_COMPANY = "enrich_company"
JOB_POSTINGS = "job_postings"
DEPARTMENT_HEADCOUNT = "department_headcount"
HEADCOUNT_GROWTH_3M = "headcount_growth_3m"
HEADCOUNT_GROWTH_6M = "headcount_growth_6m"
HEADCOUNT_GROWTH_12M = "headcount_growth_12m"
FUNDING = "funding"
SIGNALS = "signals"
# People (#83)
FIND_PEOPLE = "find_people"
# Email (#84)
FIND_EMAIL = "find_email"
VERIFY_EMAIL = "verify_email"
# LLM (#81)
STRUCTURED_OUTPUT = "structured_output"
# CRM (#85)
PUSH_CONTACT = "push_contact"
PUSH_COMPANY = "push_company"
# Outreach (V1: export only)
EXPORT = "export"
# Storage
PUT_OBJECT = "put_object"
GET_OBJECT = "get_object"
DELETE_OBJECT = "delete_object"
# Automation
TRIGGER = "trigger"
RUN_STATUS = "run_status"

#: Every feature an adapter of a category may declare. Closed on purpose.
CATEGORY_FEATURES: dict[IntegrationCategory, frozenset[str]] = {
    IntegrationCategory.LLM: frozenset({STRUCTURED_OUTPUT}),
    IntegrationCategory.COMPANY_DATA: frozenset(
        {
            SEARCH,
            ENRICH_COMPANY,
            JOB_POSTINGS,
            DEPARTMENT_HEADCOUNT,
            HEADCOUNT_GROWTH_3M,
            HEADCOUNT_GROWTH_6M,
            HEADCOUNT_GROWTH_12M,
            FUNDING,
            SIGNALS,
        }
    ),
    IntegrationCategory.PEOPLE_DATA: frozenset({FIND_PEOPLE}),
    IntegrationCategory.EMAIL_ENRICHMENT: frozenset({FIND_EMAIL}),
    IntegrationCategory.EMAIL_VERIFICATION: frozenset({VERIFY_EMAIL}),
    IntegrationCategory.CRM: frozenset({PUSH_CONTACT, PUSH_COMPANY}),
    IntegrationCategory.OUTREACH: frozenset({EXPORT}),
    IntegrationCategory.STORAGE: frozenset({PUT_OBJECT, GET_OBJECT, DELETE_OBJECT}),
    IntegrationCategory.AUTOMATION: frozenset({TRIGGER, RUN_STATUS}),
}


def features_of(category: IntegrationCategory | str) -> frozenset[str]:
    """The feature names valid for ``category``. Raises ``ValueError`` on an unknown category."""
    return CATEGORY_FEATURES[IntegrationCategory(category)]
