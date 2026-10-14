"""Website research of one company: fetch its public site and show what was found.

A thin layer over ``apps.integrations.fetch``: nothing is stored and nothing is inferred. Every page
comes with its source URL, retrieval time and content hash so it can be cited as evidence later.
A failure is part of the answer (``error``), not an HTTP error, so the UI can show why a site could
not be read (robots.txt, timeouts, blocked networks).
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.companies.models import Company
from apps.integrations.fetch import FetchError, SitePages, fetch_site_pages

EXCERPT_CHARS = 700
MAX_PAGES = 4


class OrganizationHintSerializer(serializers.Serializer[Any]):
    name = serializers.CharField()
    url = serializers.CharField()
    founding_date = serializers.CharField()
    address = serializers.CharField()


class ResearchedPageSerializer(serializers.Serializer[Any]):
    kind = serializers.CharField(help_text="home, about, products, careers, news or contact.")
    url = serializers.CharField(help_text="The page after redirects.")
    title = serializers.CharField()
    description = serializers.CharField()
    excerpt = serializers.CharField(help_text="The start of the page text (untrusted content).")
    truncated = serializers.BooleanField()
    fetched_at = serializers.DateTimeField()
    content_hash = serializers.CharField()
    robots = serializers.CharField(help_text="What robots.txt said: allowed or no_robots_txt.")
    organizations = OrganizationHintSerializer(many=True)


class SkippedPageSerializer(serializers.Serializer[Any]):
    url = serializers.CharField()
    kind = serializers.CharField()
    code = serializers.CharField(help_text="A fetch error code, or `budget`.")


class WebsiteResearchErrorSerializer(serializers.Serializer[Any]):
    code = serializers.CharField()
    message = serializers.CharField()


class WebsiteResearchSerializer(serializers.Serializer[Any]):
    company_id = serializers.UUIDField()
    website = serializers.CharField()
    pages = ResearchedPageSerializer(many=True)
    skipped = SkippedPageSerializer(many=True)
    bytes_read = serializers.IntegerField()
    error = WebsiteResearchErrorSerializer(allow_null=True)


def research_website(company: Company) -> dict[str, Any]:
    """Fetch the company's site (homepage plus up to three key pages). Never raises for a
    site-level problem: the reason is returned in ``error``."""
    body: dict[str, Any] = {
        "company_id": company.pk,
        "website": company.website,
        "pages": [],
        "skipped": [],
        "bytes_read": 0,
        "error": None,
    }
    url = company.website.strip()
    if not url:
        body["error"] = {"code": "no_website", "message": "This company has no website."}
        return body
    if "://" not in url:
        url = f"https://{url}"
    try:
        site = fetch_site_pages(url, max_pages=MAX_PAGES, purpose="company_site")
    except FetchError as exc:
        body["error"] = {"code": exc.code, "message": exc.message}
        return body
    body.update(_site_body(site))
    return body


def _site_body(site: SitePages) -> dict[str, Any]:
    pages = []
    for item in site.pages:
        result = item.result
        pages.append(
            {
                "kind": item.kind,
                "url": result.final_url,
                "title": result.title,
                "description": result.description,
                "excerpt": result.text[:EXCERPT_CHARS],
                "truncated": result.truncated or len(result.text) > EXCERPT_CHARS,
                "fetched_at": result.fetched_at,
                "content_hash": result.content_hash,
                "robots": result.robots.reason,
                "organizations": [
                    {
                        "name": org.name,
                        "url": org.url,
                        "founding_date": org.founding_date,
                        "address": org.address,
                    }
                    for org in result.organizations
                ],
            }
        )
    return {
        "pages": pages,
        "skipped": [{"url": s.url, "kind": s.kind, "code": s.code} for s in site.skipped],
        "bytes_read": site.bytes_read,
    }
