"""Duplicate detection for companies (issue #58). Read-only: it never writes.

``find_duplicate`` answers "is this company already in the campaign?" with the ordered rules
of ``docs/data-model.md`` (company identity):

1. ``domain``: the normalized domain (``normalize_domain``, what ``Company.domain`` stores) is
   equal. **Strong.** Archived companies count (``create_company`` restores them).
2. ``profile``: the canonical profile identity (``Company.profile_key``, e.g.
   ``linkedin:acme`` for any LinkedIn URL variant of that slug) is equal. **Strong.**
3. ``name_country``: the normalized name key is equal, the countries are compatible, and the
   two do not carry conflicting strong identifiers. **Weak**, active companies only.

Strong means "the same company": the caller links to it and never edits it. Weak means "maybe":
the caller creates the company and flags it. Matching is always inside one campaign; the same
domain in another campaign or client is a different row by design (one Company row per
campaign, ``docs/data-model.md``).

Countries are compatible when equal, or when either side has none (an unknown country cannot
rule a match out). Two different known countries are never a match: ``Acme`` in ``SA`` and
``Acme`` in ``AE`` are different companies. Conflicting strong identifiers are two non-null
domains that differ, or two non-null profile keys that differ: a company that clearly has
another website is not a possible duplicate on its name alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from django.db.models import Q

from apps.campaigns.models import Campaign

from .domain import normalize_domain
from .models import Company
from .normalize import name_match_key, profile_key

__all__ = ["MAX_WEAK_CANDIDATES", "DuplicateMatch", "MatchStrength", "MatchedOn", "find_duplicate"]

MAX_WEAK_CANDIDATES = 10
"""A weak match lists at most this many candidates (oldest first)."""

MatchedOn = Literal["domain", "profile", "name_country"]


class MatchStrength(StrEnum):
    STRONG = "strong"
    WEAK = "weak"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    """What ``find_duplicate`` found.

    * ``strength`` ``strong``: ``company`` is the existing company (``matched_on`` is
      ``domain`` or ``profile``; the domain wins when both match different companies).
      ``candidates`` holds every strong match, ``company`` first.
    * ``weak``: ``company`` is the oldest candidate (``matched_on`` is ``name_country``);
      ``candidates`` lists up to ``MAX_WEAK_CANDIDATES``, oldest first.
    * ``none``: ``company`` is ``None``, ``matched_on`` is ``None``, ``candidates`` is empty.
    """

    strength: MatchStrength
    company: Company | None = None
    matched_on: MatchedOn | None = None
    candidates: tuple[Company, ...] = ()

    @property
    def is_strong(self) -> bool:
        return self.strength is MatchStrength.STRONG

    @property
    def is_weak(self) -> bool:
        return self.strength is MatchStrength.WEAK


NO_MATCH = DuplicateMatch(MatchStrength.NONE)


def find_duplicate(
    campaign: Campaign,
    name: str,
    website: str = "",
    profile_url: str = "",
    country: str = "",
    *,
    exclude: Company | None = None,
) -> DuplicateMatch:
    """Look for the company in ``campaign`` that the given input already is (see module doc).

    At most two queries: one for the strong keys, one for the name key (only when there was no
    strong match). ``exclude`` leaves one company out (for checking an existing row).
    """
    domain = normalize_domain(website)
    pkey = profile_key(profile_url)
    base = Company.objects.filter(campaign=campaign)
    if exclude is not None:
        base = base.exclude(pk=exclude.pk)

    strong_filter = Q()
    if domain is not None:
        strong_filter |= Q(domain=domain)
    if pkey is not None:
        strong_filter |= Q(profile_key=pkey)
    if strong_filter:
        by_domain: list[Company] = []
        by_profile: list[Company] = []
        for company in base.filter(strong_filter).order_by("created_at", "id"):
            if domain is not None and company.domain == domain:
                by_domain.append(company)
            else:
                by_profile.append(company)
        if by_domain:
            ordered = [*by_domain, *by_profile]
            return DuplicateMatch(MatchStrength.STRONG, ordered[0], "domain", tuple(ordered))
        if by_profile:
            return DuplicateMatch(MatchStrength.STRONG, by_profile[0], "profile", tuple(by_profile))

    key = name_match_key(name)
    if not key:
        return NO_MATCH
    country = country.strip().upper()
    weak = [
        c
        for c in base.active().filter(name_key=key).order_by("created_at", "id")
        if _compatible(c, country, domain, pkey)
    ][:MAX_WEAK_CANDIDATES]
    if not weak:
        return NO_MATCH
    return DuplicateMatch(MatchStrength.WEAK, weak[0], "name_country", tuple(weak))


def _compatible(candidate: Company, country: str, domain: str | None, pkey: str | None) -> bool:
    if country and candidate.country and candidate.country != country:
        return False
    if domain is not None and candidate.domain is not None and candidate.domain != domain:
        return False
    return not (
        pkey is not None and candidate.profile_key is not None and candidate.profile_key != pkey
    )
