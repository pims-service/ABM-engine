"""``run_analysis``: website to research snapshot, signals, ICP assessment and recommendation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import (
    Classification,
    Company,
    CompanyResearch,
    DataSource,
    DataSourceType,
)
from apps.integrations.fetch import FetchError, PublicWebFetcher, fetch_site_pages
from apps.integrations.framework.errors import ProviderError
from apps.integrations.llm import find_ungrounded, pydantic_schema, structured_call
from apps.outreach.models import ActivityType
from apps.outreach.services import create_angle, record_activity
from apps.research.models import AIRecommendation, ICPAssessment, Signal, SignalType
from apps.research.services import (
    company_trigger_state,
    create_signal,
    record_ai_recommendation,
    record_icp_assessment,
)

from .freshness import expiry_for
from .prompts import (
    ASSESS_VERSION,
    EXTRACT_VERSION,
    SCHEMA_VERSION,
    assessment_prompt,
    extraction_prompt,
)
from .rules import Verdict, apply_rules
from .schemas import FactsExtraction, FitVerdict

logger = logging.getLogger(__name__)

MAX_PAGES = 5


class AnalysisFailed(Exception):
    """The analysis could not finish. ``code`` is machine readable, ``message`` is safe to show."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AnalysisOutcome:
    research: CompanyResearch
    signals: list[Signal]
    assessment: ICPAssessment
    recommendation: AIRecommendation
    dropped_signals: int


def _website_url(company: Company) -> str:
    url = (company.website or "").strip()
    if not url:
        raise AnalysisFailed("no_website", "This company has no website to research.")
    return url if "://" in url else f"https://{url}"


def _norm_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    return f"{host}{parts.path.rstrip('/')}"


def _model_name(result: Any, adapter: Any) -> str:
    configured = str(getattr(adapter, "config", {}).get("model", ""))
    return getattr(result.usage, "model", None) or configured or "unknown"


def _call(adapter: Any, prompt: Any, model: type, max_tokens: int) -> Any:
    try:
        return structured_call(adapter, prompt, pydantic_schema(model), max_tokens=max_tokens)
    except ProviderError as error:
        raise AnalysisFailed(error.code, str(error)) from error


def _count_stated(count: int | None, text: str) -> int | None:
    """Keep an employee count only when the number appears on the pages."""
    if count is None:
        return None
    needles = {str(count), f"{count:,}", f"{count:,}".replace(",", " ")}
    return count if any(n in text for n in needles) else None


def run_analysis(
    company: Company,
    adapter: Any,
    *,
    fetcher: PublicWebFetcher | None = None,
    user: User | None = None,
    now: datetime | None = None,
) -> AnalysisOutcome:
    now = now or timezone.now()
    profile = company.campaign.current_profile

    # 1. Read the public website (robots.txt honoured, social networks never fetched).
    try:
        site = fetch_site_pages(
            _website_url(company), max_pages=MAX_PAGES, purpose="company_site", fetcher=fetcher
        )
    except FetchError as error:
        raise AnalysisFailed(error.code, error.message) from error
    readable = [item for item in site.pages if item.result.text.strip()]
    if not readable:
        raise AnalysisFailed("no_content", "The website had no readable text.")
    pages = [(item.result.final_url, item.result.text) for item in readable]
    all_text = "\n".join(text for _, text in pages)

    # 2. Facts and dated signals from the pages.
    extraction = _call(
        adapter, extraction_prompt(company.name, pages, now.date()), FactsExtraction, 3000
    )
    facts: FactsExtraction = extraction.data
    extract_model = _model_name(extraction, adapter)

    with transaction.atomic():
        sources: dict[str, DataSource] = {}
        for item in readable:
            result = item.result
            source = DataSource(
                client_id=company.client_id,
                type=DataSourceType.WEBSITE,
                name=urlsplit(result.final_url).netloc[:200],
                url=result.final_url,
                retrieved_at=result.fetched_at,
                created_by=user,
            )
            source.save()
            sources[_norm_url(result.final_url)] = source
        home = sources[_norm_url(readable[0].result.final_url)]

        classification = (
            facts.classification
            if facts.classification in Classification.values
            else Classification.UNKNOWN
        )
        research = CompanyResearch(
            company=company,
            data_source=home,
            researched_at=now,
            industry=facts.industry,
            description=facts.description,
            headquarters=facts.headquarters,
            employee_count=_count_stated(facts.employee_count, all_text),
            business_model=facts.business_model,
            classification=classification,
            products_services=facts.products_services,
            target_customers=facts.target_customers,
        )
        research.save()

        dropped = 0
        for finding in facts.signals:
            signal = _record_signal(company, finding, sources, all_text, now, user, extract_model)
            if signal is None:
                dropped += 1

    state = company_trigger_state(company, now)
    record_activity(
        company,
        ActivityType.RESEARCHED,
        user,
        source=home.url,
        pages=len(readable),
        signals=len(state.signals),
        model=extract_model,
    )
    record_activity(
        company,
        ActivityType.RESEARCHED,
        user,
        source=home.url,
        pages=len(readable),
        signals=len(state.signals),
        model=extract_model,
    )

    # 3. ICP fit against the campaign rules, then the hard rules, then the recommendation.
    shown = {
        "industry": research.industry,
        "description": research.description,
        "headquarters": research.headquarters,
        "employee_count": research.employee_count,
        "business_model": research.business_model,
        "classification": research.classification,
        "products_services": research.products_services,
        "target_customers": research.target_customers,
    }
    known: dict[str, object] = {k: v for k, v in shown.items() if v not in (None, "")}
    judged = _call(
        adapter,
        assessment_prompt(profile, company.name, known, state.signals),
        FitVerdict,
        1500,
    )
    verdict: FitVerdict = judged.data
    assess_model = _model_name(judged, adapter)
    final = apply_rules(
        Verdict(
            fit=verdict.fit,
            recommendation=verdict.recommendation,
            reasons=list(verdict.reasons),
            concerns=list(verdict.concerns),
        ),
        profile,
        industry=research.industry,
        description=research.description,
        classification=research.classification,
        employee_count=research.employee_count,
    )
    explanation = verdict.explanation
    if (final.fit, final.recommendation) != (verdict.fit, verdict.recommendation):
        explanation = f"{explanation} Adjusted by the campaign rules: {final.concerns[0]}"

    with transaction.atomic():
        assessment = record_icp_assessment(
            company,
            final.fit,
            final.reasons,
            final.concerns,
            model_name=assess_model,
            prompt_version=ASSESS_VERSION,
            schema_version=SCHEMA_VERSION,
            raw_output=verdict.model_dump(),
            campaign_profile=profile,
            company_research=research,
        )
        recommendation = record_ai_recommendation(
            assessment,
            final.recommendation,
            explanation,
            model_name=assess_model,
            prompt_version=ASSESS_VERSION,
            schema_version=SCHEMA_VERSION,
            raw_output={"outreach_angle": verdict.outreach_angle},
        )
    if verdict.outreach_angle and verdict.outreach_angle.strip() and final.recommendation != "skip":
        create_angle(
            company,
            verdict.outreach_angle,
            explanation,
            signals=state.signals,
            data_sources=[home],
            model_name=assess_model,
            prompt_version=ASSESS_VERSION,
            schema_version=SCHEMA_VERSION,
            user=user,
        )
    record_activity(
        company,
        ActivityType.RECOMMENDED,
        user,
        fit=final.fit,
        recommendation=final.recommendation,
        model=assess_model,
    )
    record_activity(
        company,
        ActivityType.RECOMMENDED,
        user,
        fit=final.fit,
        recommendation=final.recommendation,
        model=assess_model,
    )
    logger.info(
        "company analysis done company=%s fit=%s recommendation=%s signals=%s dropped=%s",
        company.pk,
        final.fit,
        final.recommendation,
        len(state.signals),
        dropped,
    )
    return AnalysisOutcome(research, state.signals, assessment, recommendation, dropped)


def _record_signal(
    company: Company,
    finding: Any,
    sources: dict[str, DataSource],
    all_text: str,
    now: datetime,
    user: User | None,
    model_name: str,
) -> Signal | None:
    """Save one signal, or return None when it fails a rule (the model must not invent it)."""
    if finding.type not in SignalType.values:
        return None
    try:
        event_date = date.fromisoformat(finding.event_date)
    except ValueError:
        return None
    if event_date > now.date() + timedelta(days=1):
        return None  # a future date is a plan or an error, not an event
    source = sources.get(_norm_url(finding.source_url))
    if source is None:
        return None
    quote = finding.evidence_quote.strip()
    if len(quote) < 10 or find_ungrounded({"evidence_quote": quote}, all_text):
        return None
    if Signal.objects.filter(
        company=company, type=finding.type, event_date=event_date, supersedes__isnull=True
    ).exists():
        return None  # the same event was recorded by an earlier run
    return create_signal(
        company,
        source,
        finding.type,
        f"{finding.summary.strip()} “{quote}”",
        event_date,
        detected_at=now,
        expires_at=expiry_for(finding.type, event_date),
        model_name=model_name,
        prompt_version=EXTRACT_VERSION,
        schema_version=SCHEMA_VERSION,
        user=user,
    )
