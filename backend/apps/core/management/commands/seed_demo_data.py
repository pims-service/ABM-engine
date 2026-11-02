# ruff: noqa: E501, RUF001
# The accounts below are data: long sentences and Arabic text are intended.
"""Fill an empty install with a realistic demo: clients, campaigns, accounts at every stage.

For showing the product. Everything is invented: the companies, the people, the news and the
numbers (no real business is named or described). It is written through the same services the app
uses, so each account is a real record (website research, commercial team and growth, ICP fit,
dated signals with their sources, the AI recommendation, the human decision, people with ranks and
emails, an angle, messages, the activity timeline) and nothing is a screenshot. It never calls an
AI provider or any outside service, and the model name stored with the AI parts is ``demo-seed``.

    python manage.py seed_demo_data --yes

It refuses to run when a demo client already exists, so it adds data once and never twice. The demo
team members get no password (an administrator can set one). Remove the demo by restoring a backup
taken before it ran: the app's records are append-only and cannot be deleted one by one.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.campaigns.memberships import grant_membership
from apps.campaigns.models import Campaign, Client
from apps.campaigns.services import activate_campaign, create_campaign, create_client
from apps.companies.models import Company, CompanyStatus, ContactRole, DataSourceType
from apps.companies.services import (
    add_research_snapshot,
    create_company,
    create_contact,
    create_data_source,
)
from apps.core.roles import Role
from apps.outreach.models import ActivityType
from apps.outreach.services import approve_message, create_angle, create_message, record_activity
from apps.research.commercial import record_commercial_team
from apps.research.services import (
    create_signal,
    record_ai_recommendation,
    record_human_decision,
    record_icp_assessment,
)
from apps.research.windows import expiry_for

MODEL = "demo-seed"
PROMPT = "demo-1"
SCHEMA = "1"

TEAM = [
    ("layla.alharbi@growviah.com", "Layla Al-Harbi", Role.MANAGER),
    ("omar.siddiqui@growviah.com", "Omar Siddiqui", Role.REVIEWER),
    ("reem.nasser@growviah.com", "Reem Nasser", Role.REVIEWER),
]

# Invented trade publications (their domains are invented too).
NEWS = {
    "fin": ("Gulf Commerce Review", "gulfcommercereview.com"),
    "log": ("MENA Logistics Journal", "menalogisticsjournal.com"),
    "health": ("Arabian Health Tech Weekly", "arabianhealthtechweekly.com"),
}


def sig(kind: str, evidence: str, days: int, where: str = "news") -> dict[str, Any]:
    """A signal: ``where`` is ``news`` (a publication) or ``site`` (the company's own page)."""
    return {"kind": kind, "evidence": evidence, "days": days, "where": where}


def person(
    name: str,
    title: str,
    role: str = "none",
    email: str = "",
    status: str | None = None,
) -> dict[str, Any]:
    return {"name": name, "title": title, "role": role, "email": email, "status": status}


# Yearly headcount growth per account (negative: the team shrank), so the numbers are not uniform.
TRENDS = {
    "Tadabbur Pay": 0.34,
    "Barq Accounting": 0.18,
    "Qimam Wealth Advisory": 0.06,
    "Dalil Cloud": 0.41,
    "Mizan Audit & Assurance": -0.12,
    "Riyada Digital Lending": 0.27,
    "Nabd Insurtech": 0.22,
    "Sawaed Payroll Solutions": 0.15,
    "Amwal Capital Partners": -0.07,
    "Al Anwar Retail Bank": 0.02,
    "Hayat Wellness Stores": 0.05,
    "Marsa Freight Forwarding": 0.29,
    "Sahl Last Mile": 0.37,
    "Khaleej Cold Chain": 0.24,
    "Najd Container Lines": 0.04,
    "Wasl Customs Brokers": 0.09,
    "Zahra Home Delivery": 0.12,
    "Shifa Digital Health": 0.31,
    "Wafi Medical Group": 0.07,
    "Anaya Clinics Network": 0.19,
    "Medlink Systems": 0.11,
    "Sehati Pharmacy Chain": 0.03,
    "Nurai Telehealth": 0.38,
    "Qanun Health Insurance": 0.14,
}


def history(
    sales: int, bd: int, marketing: int, commercial: int, employees: int, trend: float
) -> list[dict[str, Any]]:
    """Team sizes 12, 6 and 3 months ago for a company whose yearly growth is ``trend``."""
    return [
        {
            "months_ago": months,
            "employees": round(employees * factor),
            "sales": max(0, round(sales * factor)),
            "bd": max(0, round(bd * factor)),
            "marketing": max(0, round(marketing * factor)),
            "commercial": max(0, round(commercial * factor)),
        }
        for months, factor in (
            (12, 1 / (1 + trend)),
            (6, 1 / (1 + trend * 0.55)),
            (3, 1 / (1 + trend * 0.27)),
        )
    ]


def acct(name: str, domain: str, country: str, city: str, **spec: Any) -> dict[str, Any]:
    return {"name": name, "domain": domain, "country": country, "city": city, **spec}


# ---------------------------------------------------------------------- SkyLight, Saudi Arabia
SKYLIGHT_KSA = [
    acct(
        "Tadabbur Pay",
        "tadabbur-pay.com",
        "SA",
        "Riyadh",
        industry="Financial Services",
        desc="Payments platform for small and mid-size merchants in Saudi Arabia: card acceptance, "
        "payment links and settlement.",
        employees=210,
        team=(18, 12, 9, 6),
        fit="strong",
        reasons=[
            "Saudi B2B payments company serving merchants",
            "210 employees, inside the 10 to 500 range",
            "An established business development team of 12",
        ],
        concerns=[],
        rec="add",
        why="Strong fit. A recent funding round and open partnership roles give a reason to reach "
        "out now, but the fit stands without them.",
        signals=[
            sig(
                "funding",
                "Closed a SAR 90 million Series B round to expand merchant acquiring across the "
                "Kingdom.",
                21,
            ),
            sig(
                "bd_hiring",
                "Hiring a Head of Partnerships and three business development managers in Riyadh.",
                9,
                "site",
            ),
        ],
        people=[
            person("Faisal Al-Otaibi", "Chief Executive Officer"),
            person(
                "Noura Al-Dossary",
                "VP Business Development",
                "primary",
                "noura.aldossary@tadabbur-pay.com",
                "verified",
            ),
            person(
                "Majed Al-Shehri",
                "Head of Partnerships",
                "secondary",
                "majed.alshehri@tadabbur-pay.com",
                "verified",
            ),
        ],
        decision=("add", "Strong fit and a live funding event. Go ahead.", "layla"),
        angle="Support the existing business development team with qualified merchant leads while "
        "they scale after the Series B, without adding SDR headcount.",
        messages=[
            (
                "email",
                "en",
                "Qualified merchant leads for your new partnerships team",
                "Hi Noura,\n\nCongratulations on the Series B. I saw you are hiring a Head of "
                "Partnerships and three business development managers in Riyadh.\n\nWe help "
                "business development teams in Saudi fintech fill the top of the pipeline with "
                "researched, qualified merchant accounts, so new hires start with warm "
                "conversations instead of cold lists.\n\nWould a short call next week be useful?\n\n"
                "Best regards,\nLayla Al-Harbi\nSkyLight",
                "approved",
            ),
        ],
        outreach="replied",
        researched=11,
    ),
    acct(
        "Barq Accounting",
        "barq-accounting.com",
        "SA",
        "Jeddah",
        industry="Accounting",
        desc="Cloud accounting and e-invoicing software for small businesses and their accountants.",
        employees=85,
        team=(9, 4, 5, 2),
        fit="strong",
        reasons=[
            "Saudi B2B accounting software company",
            "85 employees, inside the size range",
            "Sells to accounting firms, a channel we know",
        ],
        concerns=["Commercial team is small (15 people)"],
        rec="add",
        why="Strong fit; a new product launch is a natural reason to talk to their growth team.",
        signals=[
            sig(
                "new_product",
                "Launched an e-invoicing module for businesses preparing for the next phase of "
                "e-invoicing rules.",
                40,
                "site",
            )
        ],
        people=[
            person(
                "Hessa Al-Ghamdi",
                "Managing Director",
                "primary",
                "hessa@barq-accounting.com",
                "verified",
            ),
            person(
                "Tariq Bakhsh",
                "Head of Sales",
                "secondary",
                "tariq.bakhsh@barq-accounting.com",
                "unverified",
            ),
        ],
        decision=("add", "", "omar"),
        angle="Help the sales team reach accounting firms that need an e-invoicing partner now.",
        messages=[
            (
                "email",
                "en",
                "Accounting firms that need e-invoicing support",
                "Hi Hessa,\n\nI noticed Barq launched an e-invoicing module recently. Accounting "
                "firms preparing for the next phase of the rules are usually looking for exactly "
                "this.\n\nWe build researched lists of firms in the Kingdom and the reason each one "
                "may be ready to talk. Happy to share a sample of ten.\n\nBest regards,\nLayla "
                "Al-Harbi\nSkyLight",
                "draft",
            ),
        ],
        outreach="contacted",
        researched=8,
    ),
    acct(
        "Qimam Wealth Advisory",
        "qimam-wealth.com",
        "SA",
        "Riyadh",
        industry="Financial Services",
        desc="Independent wealth and corporate advisory firm serving family businesses.",
        employees=140,
        team=(10, 7, 3, 4),
        fit="strong",
        reasons=[
            "Saudi B2B financial advisory firm",
            "140 employees, inside the range",
            "A business development function of 7",
        ],
        concerns=[],
        rec="add",
        why="Strong fit with an established business development team. No trigger right now, which "
        "does not lower the fit.",
        signals=[],
        people=[
            person("Abdulaziz Al-Mutairi", "Chief Executive Officer", "primary"),
            person("Sara Al-Qahtani", "Commercial Director", "secondary"),
        ],
        decision=("add", "Eligible even without a trigger.", "layla"),
        researched=6,
    ),
    acct(
        "Dalil Cloud",
        "dalil-cloud.com",
        "SA",
        "Al Khobar",
        industry="SaaS",
        desc="HR and payroll software for mid-size employers in the Eastern Province.",
        employees=60,
        team=(7, 2, 4, 1),
        fit="strong",
        reasons=[
            "Saudi B2B SaaS company",
            "60 employees, inside the range",
            "Sales team of 7 and growing",
        ],
        concerns=["No business development function yet"],
        rec="add",
        why="Strong fit; hiring salespeople now suggests they are building pipeline.",
        signals=[
            sig(
                "sales_hiring",
                "Posted four sales executive roles in Al Khobar and Riyadh.",
                6,
                "site",
            )
        ],
        people=[
            person("Yousef Al-Dosari", "Founder and CEO"),
            person("Lina Haddad", "Head of Sales"),
        ],
        researched=4,
    ),
    acct(
        "Mizan Audit & Assurance",
        "mizan-audit.com",
        "SA",
        "Riyadh",
        industry="Accounting",
        desc="Audit, assurance and advisory firm serving large corporates and listed companies.",
        employees=320,
        team=(6, 3, 4, 2),
        fit="medium",
        reasons=["Saudi B2B professional services firm", "320 employees, inside the range"],
        concerns=[
            "Serves mostly large corporates",
            "Audit firms usually sell through partners, not a sales team",
            "Headcount is down about 12% over the year",
        ],
        rec="hold",
        why="Medium fit. Worth revisiting if they open a business development function.",
        signals=[],
        people=[person("Rakan Al-Subaie", "Managing Partner")],
        decision=("hold", "Revisit next quarter.", "reem"),
        researched=13,
    ),
    acct(
        "Riyada Digital Lending",
        "riyada-lending.com",
        "SA",
        "Riyadh",
        industry="Financial Services",
        desc="Digital lending platform for small businesses: working capital and invoice finance.",
        employees=175,
        team=(14, 9, 6, 4),
        fit="strong",
        reasons=[
            "Saudi B2B lender serving small businesses",
            "175 employees, inside the range",
            "Business development team of 9",
        ],
        concerns=["Credit regulation may slow some partnerships"],
        rec="add",
        why="Strong fit. A new commercial leader is a good moment to introduce outsourced pipeline.",
        signals=[
            sig(
                "new_leadership",
                "Appointed a Chief Commercial Officer to lead merchant partnerships.",
                14,
            )
        ],
        people=[
            person("Danah Al-Rashed", "Chief Commercial Officer"),
            person("Khalid Al-Anazi", "Head of Business Development"),
        ],
        researched=5,
    ),
    acct(
        "Nabd Insurtech",
        "nabd-insurtech.com",
        "SA",
        "Riyadh",
        industry="Technology",
        desc="Technology platform that lets brokers and insurers sell and service policies online.",
        employees=95,
        team=(8, 6, 4, 3),
        fit="strong",
        reasons=[
            "Saudi B2B technology company in insurance",
            "95 employees, inside the range",
            "Established business development team",
        ],
        concerns=[],
        rec="add",
        why="Strong fit and a distribution partnership shows they are actively adding channels.",
        signals=[
            sig(
                "major_partnership",
                "Announced a distribution partnership with a Gulf insurer to reach more brokers.",
                33,
            )
        ],
        people=[
            person("Waleed Al-Harbi", "Chief Executive Officer"),
            person(
                "Maha Al-Zahrani",
                "VP Business Development",
                "primary",
                "maha.alzahrani@nabd-insurtech.com",
                "verified",
            ),
            person(
                "Ziad Mansour",
                "Partnerships Director",
                "secondary",
                "ziad.mansour@nabd-insurtech.com",
                "verified",
            ),
        ],
        decision=("add", "", "layla"),
        angle="Help the business development team win more broker partners, building on the new "
        "insurer distribution partnership.",
        messages=[
            (
                "email",
                "en",
                "More broker partners after your insurer partnership",
                "Hi Maha,\n\nI saw the distribution partnership you announced with a Gulf insurer. "
                "Adding channels like that usually means more brokers to reach quickly.\n\nWe "
                "research brokers in the Kingdom and prepare qualified introductions for "
                "business development teams, so your people spend their time on conversations.\n\n"
                "Could we find 20 minutes this week?\n\nBest regards,\nLayla Al-Harbi\nSkyLight",
                "approved",
            ),
            (
                "email",
                "ar",
                "شركاء وسطاء جدد بعد شراكتكم مع شركة التأمين",
                "السلام عليكم أستاذة مها،\n\nاطلعت على إعلان الشراكة مع إحدى شركات التأمين الخليجية. "
                "عادةً تحتاج هذه الشراكات إلى الوصول لعدد أكبر من الوسطاء بسرعة.\n\nنقوم ببحث "
                "الوسطاء في المملكة وتجهيز تعريفات مؤهلة لفرق تطوير الأعمال، لتركّزوا على "
                "المحادثات نفسها.\n\nهل يناسبكم لقاء قصير هذا الأسبوع؟\n\nمع التحية،\nليلى الحربي\n"
                "سكاي لايت",
                "draft",
            ),
        ],
        outreach="meeting_booked",
        researched=18,
    ),
    acct(
        "Sawaed Payroll Solutions",
        "sawaed-payroll.com",
        "SA",
        "Jeddah",
        industry="SaaS",
        desc="Payroll and wage-protection software for small and mid-size companies.",
        employees=45,
        team=(5, 2, 3, 1),
        fit="strong",
        reasons=["Saudi B2B SaaS company", "45 employees, inside the range"],
        concerns=["Small team; budget for outsourced pipeline is unknown"],
        rec="add",
        why="Strong fit on the campaign rules. No current trigger.",
        signals=[],
        people=[person("Bader Al-Juhani", "Founder and CEO")],
        researched=3,
    ),
    acct(
        "Amwal Capital Partners",
        "amwal-capital.com",
        "SA",
        "Riyadh",
        industry="Financial Services",
        desc="Asset manager serving institutional investors and family offices.",
        employees=230,
        team=(3, 2, 2, 1),
        fit="medium",
        reasons=["Saudi B2B financial firm", "230 employees, inside the range"],
        concerns=["Very small commercial team (8 people)", "Sells to a small set of institutions"],
        rec="hold",
        why="Medium fit: the buyer base is too narrow for outbound lead generation.",
        signals=[],
        people=[person("Turki Al-Ajmi", "Managing Director")],
        researched=9,
    ),
    acct(
        "Al Anwar Retail Bank",
        "alanwar-bank.com",
        "SA",
        "Riyadh",
        industry="Financial Services",
        desc="Retail and business bank with a national branch network.",
        employees=4200,
        team=(120, 45, 60, 30),
        fit="medium",
        reasons=["Saudi financial services company with a large commercial team"],
        concerns=["4,200 employees is far above the campaign's size range"],
        rec="hold",
        why="Medium fit. Size is far outside the range this campaign targets.",
        signals=[],
        people=[person("Ghada Al-Mansour", "Head of SME Banking")],
        decision=("skip", "Too large for this offer.", "reem"),
        researched=15,
    ),
    acct(
        "Hayat Wellness Stores",
        "hayat-wellness.com",
        "SA",
        "Jeddah",
        industry="Retail",
        bm="B2C",
        desc="Consumer wellness and supplement stores with an online shop.",
        employees=120,
        team=(8, 0, 10, 2),
        fit="weak",
        reasons=["Saudi company inside the size range"],
        concerns=[
            "Consumer retail: not a B2B buyer of this offer",
            "Industry is not on the target list",
        ],
        rec="skip",
        why="Weak fit: a consumer retailer, outside the target industries.",
        signals=[],
        people=[],
        decision=("skip", "", "omar"),
        researched=12,
    ),
    acct("Takamul Systems", "takamul-systems.com", "SA", "Dammam"),
    acct("Rawaq Commerce", "rawaq-commerce.com", "SA", "Riyadh"),
]

# ---------------------------------------------------------------------- SkyLight, UAE logistics
SKYLIGHT_UAE = [
    acct(
        "Marsa Freight Forwarding",
        "marsa-freight.ae",
        "AE",
        "Dubai",
        industry="Logistics",
        desc="Freight forwarder moving containers and air cargo between the Gulf, Asia and Europe.",
        employees=160,
        team=(14, 6, 4, 5),
        fit="strong",
        reasons=[
            "UAE B2B freight forwarder",
            "160 employees, inside the 50 to 1,000 range",
            "A commercial team of 29",
        ],
        concerns=[],
        rec="add",
        why="Strong fit. A new Saudi office is a clear reason to reach out now.",
        signals=[
            sig(
                "new_office",
                "Opened a Dammam office to serve importers in the Eastern Province.",
                18,
                "site",
            )
        ],
        people=[
            person("Rashid Al-Falasi", "Managing Director"),
            person(
                "Priya Nair",
                "Commercial Director",
                "primary",
                "priya.nair@marsa-freight.ae",
                "verified",
            ),
            person(
                "Hamdan Al-Suwaidi",
                "Head of Business Development",
                "secondary",
                "hamdan@marsa-freight.ae",
                "unverified",
            ),
        ],
        decision=("add", "", "layla"),
        angle="Support the commercial team's Saudi expansion with qualified importer leads in the "
        "Eastern Province.",
        messages=[
            (
                "email",
                "en",
                "Importer leads for your new Dammam office",
                "Hi Priya,\n\nI saw that Marsa opened an office in Dammam. Importers in the Eastern "
                "Province are usually the first accounts a new office needs.\n\nWe research "
                "importers and the reason each may be ready to move freight, and hand the "
                "commercial team a short, qualified list.\n\nWould it help to see a sample for "
                "Dammam?\n\nBest regards,\nLayla Al-Harbi\nSkyLight",
                "approved",
            )
        ],
        outreach="contacted",
        researched=10,
    ),
    acct(
        "Sahl Last Mile",
        "sahl-lastmile.ae",
        "AE",
        "Abu Dhabi",
        industry="Logistics",
        desc="Last-mile delivery network for e-commerce retailers and marketplaces.",
        employees=240,
        team=(20, 8, 6, 4),
        fit="strong",
        reasons=[
            "UAE B2B logistics company selling to retailers",
            "240 employees, inside the range",
            "Sales and BD team of 28",
        ],
        concerns=["Competes on price with larger couriers"],
        rec="add",
        why="Strong fit; the sales hiring suggests they are building a retailer pipeline.",
        signals=[
            sig(
                "sales_hiring",
                "Advertised five account executive roles focused on e-commerce retailers.",
                11,
                "site",
            )
        ],
        people=[
            person("Omar Al-Mazrouei", "Chief Executive Officer"),
            person(
                "Sana Qureshi",
                "Sales Director",
                "primary",
                "sana.qureshi@sahl-lastmile.ae",
                "verified",
            ),
        ],
        decision=("add", "", "omar"),
        angle="Help the new account executives start with researched retailer accounts.",
        messages=[
            (
                "email",
                "en",
                "A research head start for your new account executives",
                "Hi Sana,\n\nI noticed you are hiring five account executives for e-commerce "
                "retailers. New sellers ramp faster when the first accounts are already "
                "researched.\n\nWe prepare qualified retailer lists with the reason each is worth "
                "a call. Can I send you an example?\n\nBest regards,\nLayla Al-Harbi\nSkyLight",
                "draft",
            )
        ],
        outreach="replied",
        researched=7,
    ),
    acct(
        "Khaleej Cold Chain",
        "khaleej-coldchain.ae",
        "AE",
        "Sharjah",
        industry="Logistics",
        desc="Temperature-controlled warehousing and transport for food and pharma.",
        employees=130,
        team=(9, 3, 3, 3),
        fit="strong",
        reasons=["UAE B2B logistics specialist", "130 employees, inside the range"],
        concerns=["Customer base is concentrated in a few sectors"],
        rec="add",
        why="Strong fit; fresh funding often means a push for new customers.",
        signals=[
            sig(
                "funding",
                "Raised AED 40 million to add two cold storage facilities.",
                27,
                "news",
            )
        ],
        people=[person("Mariam Al-Hosani", "Chief Executive Officer")],
        researched=5,
    ),
    acct(
        "Najd Container Lines",
        "najd-containerlines.com",
        "SA",
        "Jeddah",
        industry="Logistics",
        desc="Regional container shipping and feeder services between Red Sea and Gulf ports.",
        employees=480,
        team=(22, 8, 5, 6),
        fit="medium",
        reasons=["Saudi B2B shipping company", "Large commercial team"],
        concerns=[
            "Shipping lines sell through large agent networks",
            "Near the top of the size range",
        ],
        rec="hold",
        why="Medium fit: sales run through agents, which limits what outbound adds.",
        signals=[],
        people=[person("Nasser Al-Qahtani", "Commercial Director")],
        decision=("hold", "", "reem"),
        researched=14,
    ),
    acct(
        "Wasl Customs Brokers",
        "wasl-customs.ae",
        "AE",
        "Dubai",
        industry="Logistics",
        desc="Customs clearance and trade documentation for importers and exporters.",
        employees=55,
        team=(5, 2, 2, 1),
        fit="strong",
        reasons=["UAE B2B customs and logistics services", "55 employees, inside the range"],
        concerns=["Small commercial team"],
        rec="add",
        why="Strong fit on the campaign rules. No trigger found.",
        signals=[],
        people=[person("Imran Siddiqui", "General Manager")],
        researched=2,
    ),
    acct(
        "Zahra Home Delivery",
        "zahra-delivery.ae",
        "AE",
        "Dubai",
        industry="Logistics",
        bm="B2C",
        desc="Same-day grocery and household delivery for consumers.",
        employees=90,
        team=(4, 0, 6, 1),
        fit="weak",
        reasons=["UAE logistics company inside the size range"],
        concerns=["Serves consumers, not businesses"],
        rec="skip",
        why="Weak fit: a consumer delivery service, not a B2B logistics provider.",
        signals=[],
        people=[],
        decision=("skip", "", "omar"),
        researched=9,
    ),
    acct("Tarsheed Warehousing", "tarsheed-warehousing.ae", "AE", "Ras Al Khaimah"),
    acct("Rahal Express", "rahal-express.com", "SA", "Riyadh"),
]

# ---------------------------------------------------------------------- Nawa Cloud, healthcare
NAWA_HEALTH = [
    acct(
        "Shifa Digital Health",
        "shifa-digital.com",
        "SA",
        "Riyadh",
        industry="Healthcare IT",
        desc="Digital health platform for appointment booking, records and teleconsultation.",
        employees=260,
        team=(12, 5, 8, 3),
        fit="strong",
        reasons=[
            "Saudi healthcare technology company",
            "260 employees, inside the 100 to 2,000 range",
            "Handles patient data, so security is a priority",
        ],
        concerns=[],
        rec="add",
        why="Strong fit. A product launch brings new data flows that need monitoring.",
        signals=[
            sig(
                "new_product",
                "Launched a home-care booking service connected to partner clinics.",
                16,
                "news",
            )
        ],
        people=[
            person("Dr. Abdullah Al-Saud", "Chief Executive Officer"),
            person(
                "Ruba Al-Hamdan",
                "Chief Information Security Officer",
                "primary",
                "ruba.alhamdan@shifa-digital.com",
                "verified",
            ),
            person(
                "Anas Khoury",
                "Head of IT",
                "secondary",
                "anas.khoury@shifa-digital.com",
                "verified",
            ),
        ],
        decision=("add", "Handles patient data; clear fit.", "layla"),
        angle="Offer round-the-clock monitoring for the new home-care service and its clinic "
        "integrations.",
        messages=[
            (
                "email",
                "en",
                "Monitoring for your new home-care booking service",
                "Hi Ruba,\n\nI saw Shifa launched a home-care booking service connected to partner "
                "clinics. New integrations like that add data flows that security teams have to "
                "watch.\n\nNawa Cloud provides managed detection and response for healthcare "
                "providers in the region. Could we show you how it would cover the new service?\n\n"
                "Best regards,\nOmar Siddiqui\nNawa Cloud",
                "approved",
            )
        ],
        outreach="contacted",
        researched=9,
    ),
    acct(
        "Wafi Medical Group",
        "wafi-medical.com",
        "SA",
        "Jeddah",
        industry="Hospitals",
        desc="Hospital group with eleven hospitals and a network of outpatient centres.",
        employees=1800,
        team=(0, 3, 6, 4),
        fit="strong",
        reasons=[
            "Saudi hospital group with patient records at scale",
            "1,800 employees, inside the range",
            "A dedicated IT and security function",
        ],
        concerns=["Long procurement cycles"],
        rec="add",
        why="Strong fit; no current trigger, which does not lower the fit.",
        signals=[],
        people=[
            person(
                "Dr. Saeed Al-Ghamdi",
                "Chief Information Officer",
                "primary",
                "saeed.alghamdi@wafi-medical.com",
                "unverified",
            ),
            person("Hind Al-Otaibi", "Head of IT Security", "secondary"),
        ],
        decision=("add", "", "omar"),
        angle="Complement the in-house security team with managed detection across the hospital "
        "network.",
        messages=[
            (
                "email",
                "en",
                "Extra coverage for your hospital network",
                "Dear Dr. Saeed,\n\nHospital groups run many sites and systems that a small "
                "security team has to cover around the clock.\n\nNawa Cloud adds managed "
                "detection and response alongside an in-house team. May I send a short overview "
                "written for healthcare providers?\n\nBest regards,\nOmar Siddiqui\nNawa Cloud",
                "draft",
            )
        ],
        researched=12,
    ),
    acct(
        "Anaya Clinics Network",
        "anaya-clinics.com",
        "AE",
        "Dubai",
        industry="Healthcare",
        desc="Network of outpatient clinics offering primary care and diagnostics.",
        employees=420,
        team=(0, 2, 5, 2),
        fit="strong",
        reasons=["UAE healthcare provider", "420 employees, inside the range"],
        concerns=["Security ownership is unclear from public information"],
        rec="add",
        why="Strong fit; new clinics mean new systems to protect.",
        signals=[
            sig(
                "new_office",
                "Opened three new clinics in Abu Dhabi and Sharjah.",
                23,
                "news",
            )
        ],
        people=[person("Dr. Layal Haddad", "Chief Executive Officer")],
        researched=6,
    ),
    acct(
        "Medlink Systems",
        "medlink-systems.com",
        "KW",
        "Kuwait City",
        industry="Healthcare IT",
        desc="Software for clinics and laboratories: scheduling, billing and results.",
        employees=150,
        team=(6, 2, 3, 1),
        fit="medium",
        reasons=["Kuwaiti healthcare software company", "150 employees, inside the range"],
        concerns=["Small company; budget for managed security is unclear"],
        rec="hold",
        why="Medium fit: right industry, but a small security budget is likely.",
        signals=[],
        people=[person("Jassim Al-Kandari", "Chief Technology Officer")],
        decision=("hold", "", "reem"),
        researched=10,
    ),
    acct(
        "Sehati Pharmacy Chain",
        "sehati-pharmacy.com",
        "SA",
        "Riyadh",
        industry="Retail",
        bm="B2C",
        desc="Retail pharmacy chain selling to consumers.",
        employees=900,
        team=(10, 0, 12, 3),
        fit="weak",
        reasons=["Saudi healthcare retailer inside the size range"],
        concerns=["Retail pharmacy, not a healthcare provider", "Consumer business"],
        rec="skip",
        why="Weak fit: a consumer retailer rather than a healthcare provider.",
        signals=[],
        people=[],
        decision=("skip", "", "omar"),
        researched=8,
    ),
    acct(
        "Nurai Telehealth",
        "nurai-telehealth.com",
        "QA",
        "Doha",
        industry="Digital Health",
        desc="Telehealth consultations for employers and insurers.",
        employees=90,
        team=(4, 2, 3, 1),
        fit="medium",
        reasons=["Qatari digital health company", "Handles patient data"],
        concerns=["90 employees is slightly below the campaign's size range"],
        rec="hold",
        why="Medium fit: right industry, just under the size range.",
        signals=[],
        people=[person("Dr. Hala Al-Thani", "Founder and CEO")],
        researched=7,
    ),
    acct(
        "Qanun Health Insurance",
        "qanun-health.com",
        "SA",
        "Riyadh",
        industry="Healthcare",
        desc="Health insurer for employers, with a provider network across the Kingdom.",
        employees=640,
        team=(25, 8, 10, 6),
        fit="strong",
        reasons=[
            "Saudi health insurer with large volumes of patient data",
            "640 employees, inside the range",
        ],
        concerns=[],
        rec="add",
        why="Strong fit; a new hospital-network partnership widens the data it exchanges.",
        signals=[
            sig(
                "major_partnership",
                "Signed a provider-network agreement with a national hospital group.",
                31,
                "news",
            )
        ],
        people=[person("Nawaf Al-Zahrani", "Chief Information Officer")],
        researched=4,
    ),
    acct("Dar Al Sihha Hospital", "daralsihha-hospital.com", "SA", "Madinah"),
]

CLIENTS: list[dict[str, Any]] = [
    {
        "name": "SkyLight",
        "notes": "B2B outbound and lead generation agency, Riyadh.",
        "campaigns": [
            {
                "name": "KSA Financial Services & SaaS, Q4",
                "data": {
                    "offer": "B2B outbound lead generation",
                    "countries": ["SA"],
                    "industries": ["Financial Services", "Accounting", "SaaS", "Technology"],
                    "company_size_min": 10,
                    "company_size_max": 500,
                    "business_model": "b2b",
                    "excluded_industries": ["Government", "Retail"],
                    "excluded_company_types": ["Non-profit"],
                    "target_departments": [
                        "Sales",
                        "Business Development",
                        "Commercial",
                        "Partnerships",
                    ],
                    "preferred_buyer_titles": [
                        "VP Business Development",
                        "Head of Business Development",
                        "Commercial Director",
                        "Sales Director",
                        "CEO",
                        "Founder",
                        "Managing Director",
                    ],
                    "outreach_languages": ["ar", "en"],
                    "custom_rules": "Prefer companies that already have a business development "
                    "team. A trigger is a plus, never a requirement.",
                },
                "accounts": SKYLIGHT_KSA,
                "news": "fin",
            },
            {
                "name": "UAE Logistics & Freight, Q4",
                "data": {
                    "offer": "Outsourced pipeline research for logistics providers",
                    "countries": ["AE", "SA"],
                    "industries": ["Logistics", "Freight Forwarding", "Supply Chain"],
                    "company_size_min": 50,
                    "company_size_max": 1000,
                    "business_model": "b2b",
                    "excluded_industries": ["Government"],
                    "excluded_company_types": [],
                    "target_departments": ["Sales", "Commercial", "Business Development"],
                    "preferred_buyer_titles": [
                        "Commercial Director",
                        "Sales Director",
                        "Head of Business Development",
                        "Managing Director",
                    ],
                    "outreach_languages": ["en"],
                    "custom_rules": "B2B logistics only. Consumer delivery services are out.",
                },
                "accounts": SKYLIGHT_UAE,
                "news": "log",
            },
        ],
    },
    {
        "name": "Nawa Cloud",
        "notes": "Managed security provider for healthcare organisations in the Gulf.",
        "campaigns": [
            {
                "name": "GCC Healthcare IT, managed security",
                "data": {
                    "offer": "Managed detection and response for healthcare providers",
                    "countries": ["SA", "AE", "KW", "QA"],
                    "industries": ["Healthcare IT", "Hospitals", "Digital Health", "Healthcare"],
                    "company_size_min": 100,
                    "company_size_max": 2000,
                    "business_model": "b2b",
                    "excluded_industries": ["Retail"],
                    "excluded_company_types": [],
                    "target_departments": ["IT", "Security", "Compliance"],
                    "preferred_buyer_titles": [
                        "Chief Information Security Officer",
                        "Chief Information Officer",
                        "Head of IT Security",
                        "Head of IT",
                        "Chief Technology Officer",
                    ],
                    "outreach_languages": ["en", "ar"],
                    "custom_rules": "Providers that handle patient data. Pharmacies and consumer "
                    "retail are out.",
                },
                "accounts": NAWA_HEALTH,
                "news": "health",
            }
        ],
    },
]


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-")


class Command(BaseCommand):
    help = "Add a realistic, invented demo (clients, campaigns, accounts at every stage)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--yes", action="store_true", help="Confirm: this writes demo data.")

    def handle(self, *args: Any, **options: Any) -> None:
        if not options["yes"]:
            raise CommandError("This adds demo clients and accounts. Pass --yes to confirm.")
        names = [c["name"] for c in CLIENTS]
        if Client.objects.filter(name__in=names).exists():
            raise CommandError("A demo client already exists; the demo is added only once.")
        admin = get_user_model().objects.filter(is_superuser=True).order_by("created_at").first()
        if admin is None:
            raise CommandError("There is no administrator to own the demo.")
        with transaction.atomic():
            people = self._team()
            for spec in CLIENTS:
                client = create_client(spec["name"], spec["notes"], admin)
                for user, _, role in people.values():
                    grant_membership(client, user, role, audit_actor=admin)
                for campaign_spec in spec["campaigns"]:
                    self._campaign(client, campaign_spec, people, admin)
        self.stdout.write(
            self.style.SUCCESS(
                f"Added {len(CLIENTS)} clients with "
                f"{sum(len(c['accounts']) for s in CLIENTS for c in s['campaigns'])} accounts."
            )
        )

    def _team(self) -> dict[str, tuple[Any, str, str]]:
        users: dict[str, tuple[Any, str, str]] = {}
        model = get_user_model()
        for email, name, role in TEAM:
            user = model.objects.filter(email=email).first()
            if user is None:
                user = model.objects.create_user(email=email, password=None, name=name)
            users[name.split()[0].lower()] = (user, name, role)
        return users

    def _campaign(
        self, client: Client, spec: dict[str, Any], team: dict[str, Any], admin: Any
    ) -> None:
        campaign = create_campaign(client, spec["name"], spec["data"], admin)
        activate_campaign(campaign, admin)
        campaign.refresh_from_db()
        publication = NEWS[spec["news"]]
        for index, account in enumerate(spec["accounts"]):
            self._account(campaign, account, team, publication, index, admin)

    def _account(
        self,
        campaign: Campaign,
        spec: dict[str, Any],
        team: dict[str, Any],
        publication: tuple[str, str],
        index: int,
        admin: Any,
    ) -> None:
        now = timezone.now()
        result = create_company(
            campaign,
            spec["name"],
            f"https://www.{spec['domain']}",
            country=spec["country"],
            user=admin,
        )
        company: Company = result.company
        if "industry" not in spec:
            return  # waiting to be analysed
        client = campaign.client

        def reviewer(key: str) -> Any:
            return team[key][0]

        researched_at = now - timedelta(days=spec["researched"], hours=index % 5)
        site = create_data_source(
            client,
            DataSourceType.WEBSITE,
            urlsplit(f"https://www.{spec['domain']}").netloc,
            url=f"https://www.{spec['domain']}/about",
            retrieved_at=researched_at,
            user=admin,
        )
        bm = spec.get("bm", "B2B")
        snapshot = add_research_snapshot(
            company,
            site,
            researched_at=researched_at,
            industry=spec["industry"],
            description=spec["desc"],
            headquarters=f"{spec['city']}, {spec['country']}",
            employee_count=spec["employees"],
            business_model="Business to business" if bm == "B2B" else "Business to consumer",
            classification=bm.lower(),
        )
        sales, bd, marketing, commercial = spec["team"]
        record_commercial_team(
            company,
            {
                "source_note": "Analyst notes from the company's public team and careers pages",
                "as_of": (researched_at + timedelta(hours=2)).date(),
                "employees": spec["employees"],
                "sales": sales,
                "bd": bd,
                "marketing": marketing,
                "commercial": commercial,
                "history": history(
                    sales,
                    bd,
                    marketing,
                    commercial,
                    spec["employees"],
                    TRENDS.get(spec["name"], 0.2),
                ),
            },
            reviewer("layla"),
        )
        assessment = record_icp_assessment(
            company,
            spec["fit"],
            spec["reasons"],
            spec["concerns"],
            model_name=MODEL,
            prompt_version=PROMPT,
            schema_version=SCHEMA,
            company_research=snapshot,
            created_at=researched_at + timedelta(hours=3),
        )
        signals = []
        sources = [site]
        for item in spec["signals"]:
            event = (now - timedelta(days=item["days"])).date()
            if item["where"] == "news":
                title, host = publication
                source = create_data_source(
                    client,
                    DataSourceType.NEWS,
                    title,
                    url=f"https://{host}/{_slug(spec['name'])}-{_slug(item['kind'])}",
                    evidence_date=event,
                    retrieved_at=now - timedelta(days=max(1, item["days"] - 1)),
                    user=admin,
                )
            else:
                page = "careers" if item["kind"].endswith("hiring") else "news"
                source = create_data_source(
                    client,
                    DataSourceType.WEBSITE,
                    urlsplit(f"https://www.{spec['domain']}").netloc,
                    url=f"https://www.{spec['domain']}/{page}",
                    evidence_date=event,
                    retrieved_at=now - timedelta(days=max(1, item["days"] - 1)),
                    user=admin,
                )
            sources.append(source)
            signals.append(
                create_signal(
                    company,
                    source,
                    item["kind"],
                    item["evidence"],
                    event,
                    expires_at=expiry_for(item["kind"], event),
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
            created_at=researched_at + timedelta(hours=3, minutes=5),
        )
        record_activity(
            company,
            ActivityType.RESEARCHED,
            reviewer("layla"),
            created_at=researched_at,
            source="website and public pages",
        )
        record_activity(
            company,
            ActivityType.RECOMMENDED,
            None,
            created_at=researched_at + timedelta(hours=3, minutes=5),
            recommendation=spec["rec"],
        )
        contacts = []
        for rank, item in enumerate(spec["people"], start=1):
            contacts.append(
                create_contact(
                    company,
                    site,
                    item["name"],
                    title=item["title"],
                    email=item["email"],
                    email_status=item["status"],
                    rank=rank,
                    role=ContactRole(item["role"]),
                    relevance_reason=f"{item['title']}: {_why_person(item['title'])}",
                    user=admin,
                )
            )
        decided_at = None
        if spec.get("decision"):
            choice, note, who = spec["decision"]
            decided_at = researched_at + timedelta(days=1, hours=2)
            decider = reviewer(who)
            record = record_human_decision(
                company,
                choice,
                decider,
                ai_recommendation=recommendation,
                note=note,
                decided_at=decided_at,
            )
            record_activity(
                company,
                ActivityType.DECIDED,
                decider,
                created_at=decided_at,
                decision=record.decision,
                note=record.note,
                agrees_with_ai=recommendation.status == record.decision,
            )
        if spec.get("angle") and spec.get("decision", ("",))[0] == "add":
            self._outreach(company, spec, signals, sources, contacts, team, decided_at)
        stages = {
            "contacted": ["contacted"],
            "replied": ["contacted", "replied"],
            "meeting_booked": ["contacted", "replied", "meeting_booked"],
        }.get(spec.get("outreach") or "", [])
        for step, stage in enumerate(stages, start=1):
            moment = (decided_at or researched_at) + timedelta(days=2 * step, hours=step)
            record_activity(
                company,
                ActivityType(stage),
                reviewer("layla"),
                created_at=min(moment, now - timedelta(hours=1)),
            )
        company.status = CompanyStatus.ANALYZED
        company.save(update_fields=["status", "updated_at"])

    def _outreach(
        self,
        company: Company,
        spec: dict[str, Any],
        signals: list[Any],
        sources: list[Any],
        contacts: list[Any],
        team: dict[str, Any],
        decided_at: Any,
    ) -> None:
        target = next((c for c in contacts if c.role == ContactRole.PRIMARY), None)
        angle = create_angle(
            company,
            spec["angle"],
            spec["why"],
            signals=signals,
            data_sources=sources[:1],
            model_name=MODEL,
            prompt_version=PROMPT,
            schema_version=SCHEMA,
        )
        if target is None:
            return
        for channel, language, subject, body, status in spec.get("messages", []):
            message = create_message(
                angle,
                target,
                channel,
                language,
                body,
                subject=subject,
                signals=signals,
                model_name=MODEL,
                prompt_version=PROMPT,
                schema_version=SCHEMA,
            )
            if status == "approved":
                approve_message(message, team["layla"][0])


def _why_person(title: str) -> str:
    lowered = title.lower()
    if "business development" in lowered or "partnership" in lowered:
        return "owns partnerships and new business."
    if "commercial" in lowered or "sales" in lowered:
        return "leads the commercial team."
    if "security" in lowered or "information" in lowered or "it " in lowered + " ":
        return "owns security and the systems that hold patient data."
    return "decides on new vendors for the company."
