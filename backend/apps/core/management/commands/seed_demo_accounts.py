"""Fill the demo campaigns with finished sample accounts, so the dashboard has something to show.

Everything is invented and marked: names end with "(sample)", websites are on ``example.com``,
people are generic and every data source starts with ``[SAMPLE DATA]``. Rows go through the same
services the app uses, so the results are real records (research snapshot, ICP fit, signals, AI
recommendation, human decision, people, angle, activity), not screenshots. It never calls an AI
provider. Idempotent: a company that already exists is left alone. Refuses to run when DEBUG is
off unless ``--force`` is given.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.campaigns.models import Campaign
from apps.companies.models import Company, CompanyStatus, ContactRole, DataSourceType
from apps.companies.services import (
    add_research_snapshot,
    create_company,
    create_contact,
    create_data_source,
)
from apps.core.seed_sample import SAMPLE_MARKER
from apps.outreach.models import ActivityType
from apps.outreach.services import create_angle, record_activity
from apps.research.engine.freshness import expiry_for
from apps.research.models import Signal
from apps.research.services import (
    create_signal,
    record_ai_recommendation,
    record_human_decision,
    record_icp_assessment,
)

MODEL = "sample-data"
PROMPT = "sample-v1"
SCHEMA = "1"


def account(**fields: Any) -> dict[str, Any]:
    return fields


# One list per campaign name. ``signals`` are (type, evidence, days ago); ``people`` are
# (name, title, role); ``decision`` is add/hold/skip or None (waiting for a review);
# ``outreach`` is None, "contacted", "replied" or "meeting_booked".
MERIDIAN: list[dict[str, Any]] = [
    account(
        name="Atlas Freight Group (sample)",
        domain="atlas-freight",
        country="AE",
        industry="Logistics",
        employees=420,
        hq="Dubai, UAE",
        bm="B2B",
        desc="Regional freight forwarder running six warehouses across the UAE.",
        sales=34,
        bd=9,
        marketing=6,
        change12=14.0,
        fit="strong",
        reasons=[
            "UAE logistics company in the 100 to 1000 size range",
            "Runs its own warehouses, which the campaign prefers",
            "Has an IT team of its own to buy for",
        ],
        concerns=[],
        rec="add",
        why="Strong fit on every rule; a recent expansion makes the timing good.",
        signals=[("new_office", "Opened a fourth warehouse in Jebel Ali.", 21)],
        decision=None,
        people=[("Layla Haddad", "Head of IT", "primary"), ("Omar Nasser", "CIO", "secondary")],
        angle="Support the IT team running a bigger warehouse estate with managed security.",
    ),
    account(
        name="Dune Health Systems (sample)",
        domain="dune-health",
        country="AE",
        industry="Healthcare",
        employees=120,
        hq="Abu Dhabi, UAE",
        bm="B2B",
        desc="Clinic management software and a network of day clinics.",
        sales=8,
        bd=3,
        marketing=4,
        change12=22.0,
        fit="strong",
        reasons=[
            "Healthcare company inside the size range",
            "Handles patient data, so security matters to the buyer",
        ],
        concerns=["Part of the business is B2C clinics"],
        rec="add",
        why="A strong fit; the new CISO is a likely buyer.",
        signals=[("new_leadership", "Appointed a Chief Information Security Officer.", 12)],
        decision="add",
        people=[("Sara Haddad", "CISO", "primary"), ("Karim Aziz", "IT Director", "secondary")],
        angle="Help the new CISO build a security programme without hiring a full team first.",
        outreach="contacted",
    ),
    account(
        name="Gulf Cart Retail (sample)",
        domain="gulf-cart",
        country="AE",
        industry="Retail",
        employees=760,
        hq="Dubai, UAE",
        bm="B2C",
        desc="Online and in-store grocery retailer.",
        sales=12,
        bd=2,
        marketing=30,
        change12=-5.0,
        fit="medium",
        reasons=["Retail company inside the size range"],
        concerns=["Mostly B2C", "Headcount is falling"],
        rec="hold",
        why="The industry fits but the business is mostly B2C; worth another look later.",
        signals=[],
        decision=None,
        people=[("Rami Saeed", "IT Director", "none")],
    ),
    account(
        name="Falcon Logistics Hub (sample)",
        domain="falcon-hub",
        country="AE",
        industry="Logistics",
        employees=240,
        hq="Sharjah, UAE",
        bm="B2B",
        desc="Warehousing and last-mile delivery for online sellers.",
        sales=15,
        bd=4,
        marketing=3,
        change12=31.0,
        fit="strong",
        reasons=[
            "UAE logistics company inside the size range",
            "Runs its own warehouses",
            "Growing quickly, so security needs are rising",
        ],
        concerns=[],
        rec="add",
        why="Strong fit and fast growth.",
        signals=[
            ("funding", "Raised a growth round to fund two new sites.", 40),
            ("headcount_growth", "Headcount up 31 percent over twelve months.", 30),
        ],
        decision=None,
        people=[("Noor Mansour", "CIO", "primary"), ("Hadi Farouk", "Head of IT", "secondary")],
        angle="Back the growth plan with security that scales with each new site.",
    ),
    account(
        name="Pearl Clinics Network (sample)",
        domain="pearl-clinics",
        country="AE",
        industry="Healthcare",
        employees=95,
        hq="Dubai, UAE",
        bm="B2C",
        desc="Dental and dermatology clinics for individual patients.",
        sales=3,
        bd=0,
        marketing=7,
        change12=4.0,
        fit="weak",
        reasons=["Healthcare company"],
        concerns=["Below the size range", "Sells to individuals, not businesses"],
        rec="skip",
        why="Too small and a B2C model.",
        signals=[],
        decision="skip",
        people=[],
    ),
    account(
        name="Desert Route Transport (sample)",
        domain="desert-route",
        country="AE",
        industry="Logistics",
        employees=310,
        hq="Ras Al Khaimah, UAE",
        bm="B2B",
        desc="Trucking and cold-chain transport for manufacturers.",
        sales=18,
        bd=5,
        marketing=2,
        change12=2.0,
        fit="strong",
        reasons=[
            "UAE logistics company inside the size range",
            "Operates its own cold storage sites",
        ],
        concerns=["No recent business events found"],
        rec="add",
        why="A good prospect even without a trigger.",
        signals=[],
        decision="add",
        people=[("Ziad Karam", "IT Director", "primary")],
        angle="Protect the cold-chain systems the business depends on every day.",
        outreach="replied",
    ),
    account(name="Oasis Retail Group (sample)", domain="oasis-retail", country="AE"),
    account(name="Skyline Parcels (sample)", domain="skyline-parcels", country="AE"),
]

SKYLIGHT: list[dict[str, Any]] = [
    account(
        name="Tiqmo Pay (sample)",
        domain="tiqmo-pay",
        country="SA",
        industry="Financial Services",
        employees=155,
        hq="Riyadh, Saudi Arabia",
        bm="B2B",
        desc="Digital payments and remittances for businesses.",
        sales=22,
        bd=15,
        marketing=9,
        change12=-12.0,
        fit="strong",
        reasons=[
            "Saudi B2B financial-services company",
            "155 employees, inside the 10 to 500 range",
            "Established Business Development organisation",
        ],
        concerns=["Overall headcount is declining"],
        rec="add",
        why="Strong fit; no trigger, which does not rule it out.",
        signals=[],
        decision=None,
        people=[
            ("Fahad Al Otaibi", "Managing Director", "primary"),
            ("Dana Khalil", "Chief Marketing Officer", "secondary"),
        ],
        angle="Support the existing BD organisation with targeted, qualified B2B opportunities.",
    ),
    account(
        name="Najd Ledger Accounting (sample)",
        domain="najd-ledger",
        country="SA",
        industry="Accounting",
        employees=60,
        hq="Riyadh, Saudi Arabia",
        bm="B2B",
        desc="Accounting and audit services for mid-size companies.",
        sales=5,
        bd=2,
        marketing=1,
        change12=18.0,
        fit="medium",
        reasons=["Saudi B2B accounting firm", "Growing"],
        concerns=["Small commercial team"],
        rec="add",
        why="A medium fit with a hiring signal.",
        signals=[("bd_hiring", "Advertised two Business Development roles.", 9)],
        decision=None,
        people=[("Abdullah Rashid", "Founder", "primary")],
        angle="Build outbound pipeline without a full internal SDR team.",
    ),
    account(
        name="Hijaz Cloud Works (sample)",
        domain="hijaz-cloud",
        country="SA",
        industry="SaaS",
        employees=210,
        hq="Jeddah, Saudi Arabia",
        bm="B2B",
        desc="Workflow software for logistics and retail businesses.",
        sales=28,
        bd=11,
        marketing=14,
        change12=26.0,
        fit="strong",
        reasons=["Saudi B2B SaaS company", "Established BD team", "Fast growing"],
        concerns=[],
        rec="add",
        why="Strong fit and strong momentum.",
        signals=[
            ("partnerships_hiring", "Hiring a Partnerships Director.", 15),
            ("market_expansion", "Announced entry into the UAE market.", 33),
        ],
        decision="add",
        people=[
            ("Majed Al Harbi", "VP BD", "primary"),
            ("Reem Saleh", "Sales Director", "secondary"),
        ],
        angle="Add qualified UAE opportunities to the team that is opening the market.",
        outreach="meeting_booked",
    ),
    account(
        name="Red Dune Consulting (sample)",
        domain="red-dune",
        country="SA",
        industry="Technology",
        employees=35,
        hq="Dammam, Saudi Arabia",
        bm="B2B",
        desc="IT consulting for energy companies.",
        sales=2,
        bd=1,
        marketing=0,
        change12=0.0,
        fit="medium",
        reasons=["Saudi B2B technology firm"],
        concerns=["Very small commercial team", "No growth"],
        rec="hold",
        why="Small and flat; revisit if it starts hiring.",
        signals=[],
        decision="hold",
        people=[("Yousef Al Sharif", "CEO", "none")],
    ),
    account(
        name="Kingdom Wholesale Foods (sample)",
        domain="kingdom-foods",
        country="SA",
        industry="Food Distribution",
        employees=480,
        hq="Riyadh, Saudi Arabia",
        bm="B2B",
        desc="Wholesale food distribution.",
        sales=40,
        bd=3,
        marketing=5,
        change12=3.0,
        fit="weak",
        reasons=["Saudi B2B company inside the size range"],
        concerns=["Industry is outside the target list"],
        rec="skip",
        why="The industry is not one we serve.",
        signals=[],
        decision=None,
        people=[],
    ),
    account(name="Gulf Pay Bridge (sample)", domain="gulf-pay-bridge", country="SA"),
]

DATA: dict[str, list[dict[str, Any]]] = {
    "Meridian UAE cloud security": MERIDIAN,
    "SkyLight KSA outbound": SKYLIGHT,
}


class Command(BaseCommand):
    help = "Add finished sample accounts to the demo campaigns (idempotent; DEBUG only)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--force", action="store_true", help="Run even if DEBUG is off.")

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG and not options["force"]:
            raise CommandError("Refusing to seed demo data while DEBUG is off. Pass --force.")
        email = os.environ.get("DEV_SUPERUSER_EMAIL", "")
        user = get_user_model().objects.filter(email=email).first()
        if user is None:
            raise CommandError("Run seed_dev_data first (the dev superuser is missing).")
        added = 0
        for campaign in Campaign.objects.filter(name__in=list(DATA)):
            for spec in DATA[campaign.name]:
                with transaction.atomic():
                    added += self._seed(campaign, spec, user)
        self.stdout.write(f"Added {added} sample accounts.")

    def _seed(self, campaign: Campaign, spec: dict[str, Any], user: Any) -> int:
        result = create_company(
            campaign,
            spec["name"],
            f"https://{spec['domain']}.example.com",
            country=spec["country"],
            user=user,
        )
        company: Company = result.company
        if not result.created or "industry" not in spec:
            return int(result.created)
        client = campaign.client
        now = timezone.now()
        source = create_data_source(
            client, DataSourceType.MANUAL, f"{SAMPLE_MARKER} demo research", user=user
        )
        research = add_research_snapshot(
            company,
            source,
            researched_at=now - timedelta(days=2),
            industry=spec["industry"],
            description=spec["desc"],
            headquarters=spec["hq"],
            employee_count=spec["employees"],
            business_model=spec["bm"],
            classification=spec["bm"].lower() if spec["bm"] in ("B2B", "B2C") else None,
            sales_headcount=spec["sales"],
            bd_headcount=spec["bd"],
            marketing_headcount=spec["marketing"],
            headcount_change_12m=spec["change12"],
        )
        assessment = record_icp_assessment(
            company,
            spec["fit"],
            spec["reasons"],
            spec["concerns"],
            model_name=MODEL,
            prompt_version=PROMPT,
            schema_version=SCHEMA,
            company_research=research,
        )
        signals: list[Signal] = []
        for kind, evidence, days in spec["signals"]:
            event = (now - timedelta(days=days)).date()
            page = create_data_source(
                client,
                DataSourceType.WEBSITE,
                f"{SAMPLE_MARKER} company news page",
                url=f"https://{spec['domain']}.example.com/news",
                user=user,
            )
            signals.append(
                create_signal(
                    company,
                    page,
                    kind,
                    evidence,
                    event,
                    expires_at=expiry_for(kind, event),
                    model_name=MODEL,
                    prompt_version=PROMPT,
                    schema_version=SCHEMA,
                )
            )
        recommendation = record_ai_recommendation(
            assessment,
            spec["rec"],
            spec["why"],
            model_name=MODEL,
            prompt_version=PROMPT,
            schema_version=SCHEMA,
        )
        record_activity(company, ActivityType.RESEARCHED, None, summary="Sample research added")
        record_activity(company, ActivityType.RECOMMENDED, None, recommendation=spec["rec"])
        if spec.get("angle"):
            create_angle(
                company,
                spec["angle"],
                spec["why"],
                signals=signals,
                model_name=MODEL,
                prompt_version=PROMPT,
                schema_version=SCHEMA,
            )
        for rank, (name, title, role) in enumerate(spec["people"], start=1):
            create_contact(
                company,
                source,
                name,
                title=title,
                rank=rank,
                role=ContactRole(role),
                relevance_reason=f"{SAMPLE_MARKER} {title}",
                user=user,
            )
        if spec.get("decision"):
            decided = record_human_decision(
                company, spec["decision"], user, ai_recommendation=recommendation
            )
            record_activity(
                company,
                ActivityType.DECIDED,
                user,
                decision=decided.decision,
                note="",
                agrees_with_ai=recommendation.status == decided.decision,
            )
        for stage in {
            "contacted": ["contacted"],
            "replied": ["contacted", "replied"],
            "meeting_booked": ["contacted", "replied", "meeting_booked"],
        }.get(spec.get("outreach") or "", []):
            record_activity(company, ActivityType(stage), user)
        company.status = CompanyStatus.ANALYZED
        company.save(update_fields=["status", "updated_at"])
        return 1
