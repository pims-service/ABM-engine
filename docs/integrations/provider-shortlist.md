# Provider shortlist and licensing review

This is the write-up for the provider spike (issue #73, part of the M3 tracking
issue #72). It shortlists real providers for each integration category in Brief
§17, records what we could verify about their APIs and terms, and proposes a
first licensed company-data provider and a first people and email stack.

It is a research document, not a purchase order. **The commercial decision is
the project owner's.** Nothing here recommends signing a contract; see
[ADR 0012](../adr/0012-first-provider-selection.md) for the decision and the
open questions.

How to read it:

- Research date: **2026-10-10**. Vendors change prices, plans and terms often.
  Everything marked **verify before committing** must be re-checked on the
  vendor's site, and anything legal must be read in the signed contract, not
  here.
- Evidence level is tagged on each claim: **[official]** means we read it on the
  vendor's own documentation or pricing page. **[third-party]** means it came
  from a review site, blog or search summary and could be stale or wrong.
  **[not verified]** means we could not find or confirm it.
- We did not create accounts, call any API or read any signed terms. No keys
  were used. Nothing below is legal advice.
- Prices are quoted only where the vendor publishes them. Where they do not, we
  say so rather than estimate.

## What the brief needs from providers

| Brief reference | Need |
| --- | --- |
| §4, §17 | Licensed or official APIs only, no unauthorized LinkedIn scraping, no hard-coding to one provider |
| §5 company | Name, website, industry, description, HQ, employee count |
| §5 structure | Headcount by department (sales, BD, marketing, commercial) |
| §5 growth | 3, 6 and 12 month headcount change |
| §5 signals | Funding, job openings, leadership hires, expansion, news; source and date stored |
| §8 | Candidate people: name, title, profile URL, email where available |
| §12 | Enrich email, then verify it, for approved accounts only |
| §17 | LLM, company data, people data, email enrichment, email verification, CRM (GoHighLevel), outreach, storage, jobs |
| Brief §3 example | Saudi Arabia, Arabic and English outreach |

## Excluded approaches

We exclude anything whose data or access depends on scraping LinkedIn (or
another site) against its terms, for four reasons.

1. **The brief rules it out** (§4) and [ADR 0002](../adr/0002-modular-provider-adapters.md)
   repeats it.
2. **Providers in this niche have been shut down.** LinkedIn sued Nubela, the
   company behind the Proxycurl LinkedIn data API, in January 2025; Proxycurl
   closed on 4 July 2025 [third-party:
   [Unipile](https://www.unipile.com/proxycurl-alternative/),
   [Nubela's own post](https://nubela.co/blog/goodbye-proxycurl/),
   retrieved 2026-10-10]. LinkedIn also sued ProAPIs in October 2025 over its
   iScraper product [third-party:
   [Security Affairs](https://securityaffairs.com/183001/security/linkedin-sues-proapis-for-15k-month-linkedin-data-scraping-scheme.html),
   retrieved 2026-10-10]. A feature built on such a vendor can disappear
   overnight.
3. **LinkedIn's own APIs do not cover our use.** The self-serve tiers return only
   the logged-in member's data, the Marketing Developer Platform is for pages
   and ads and is partner-gated, and the Sales Navigator API was reported as
   closed to new partners [third-party:
   [Phyllo](https://www.getphyllo.com/post/linkedin-api-access-in-2026-partner-program-approval-timeline-alternatives),
   retrieved 2026-10-10; **verify** on LinkedIn's developer docs]. There is no
   legitimate "look up this company's departments" LinkedIn API for us.
4. **Account and legal risk lands on the customer**, not only the vendor.

So we exclude: Proxycurl-style LinkedIn profile APIs (defunct or
scraping-based), browser extensions or "Sales Navigator exporters" that drive a
logged-in LinkedIn session, generic scraping platforms and marketplace actors
pointed at LinkedIn, and any provider that cannot say how its data was
collected. Hosts of scraper actors (for example Apify marketplace actors) are
not a "provider" for this project.

A caution that applies even to licensed vendors: many aggregators build
profiles from public web data that includes LinkedIn-derived fields. A vendor
being "licensed to us" does not mean LinkedIn agrees with how it collected.
That is a vendor-risk question for the contract (see the checklist below), not
something we can settle from documentation.

## 1. Company data, headcount, growth and signals

### Candidates

| | People Data Labs (PDL) | Coresignal | Apollo.io (API) | Crunchbase API | Lusha (company API) |
| --- | --- | --- | --- | --- | --- |
| Access | REST, API key [official] | REST, API key; also datasets and dashboards [official] | REST, API key (paid plans) [third-party] | REST, enterprise contract [third-party] | REST, paid plans from Pro [third-party] |
| Company lookup | `POST/GET /v5/company/enrich` by name, website, ticker or social profile; billed per match only [official] | Company base and multi-source APIs, search, enrichment [official] | Organization enrichment and search [third-party] | Organizations, funding rounds [third-party] | Company enrichment by domain or name [third-party] |
| Industry, description, HQ | Yes (firmographics; coverage 90% location, 75% industry in v33.1) [third-party summary of PDL changelog] | "600+ fields" including firmographics [official] | Yes [third-party] | Yes [third-party] | Yes [third-party] |
| Employee count | `employee_count`, based on resume data; 37% fill rate across all records in v33.1 [third-party] | Yes [official claim] | Yes [third-party] | Estimates [not verified] | Yes [third-party] |
| Headcount by department | `employee_count_by_role` is an **add-on in the Premium bundle**, not in base self-serve [official feedback page] | "workforce intelligence" and a separate **Historical Headcount API** (10 credits) [official]; exact field names [not verified] | Department headcount [not verified] | No [not verified] | Not documented [not verified] |
| 3/6/12 month growth | `employee_growth_rate.3_month`, `.6_month`, `.12_month`, in Premium and Comprehensive bundles [third-party from PDL docs search]; by-role growth is 12-month only | Historical headcount supports it; ready-made 3/6/12 fields [not verified] | Not documented [not verified] | No | No |
| Funding | `latest_funding_stage`, funding details bundle [official] | Funding rounds [third-party] | Funding data [third-party] | Strongest: funding rounds, investors [third-party] | Not documented |
| Job postings | Job data from company career pages [official] | Separate Jobs API, 1 credit per job [official] | Jobs endpoints [not verified] | No | Signals API [third-party] |
| Pricing | Free: 100 lookups/month. Pro: about $98/month for 350 person credits and 1,000 company lookups [third-party]; Enterprise custom. **Verify before committing.** | Free trial: 2,000 credits for 7 days. Plans from Mini $49/month (2,500 credits) to Elite $5,000/month. Company API 20 credits, Historical Headcount 10, Jobs 1 [official, retrieved 2026-10-10] | Credits shared with app plan; 1 credit per organization or person page; free tier exists [third-party]. **Verify.** | Quote-only enterprise; old free API tier retired [third-party]; **verify** | API from the Pro plan; billed per row returned [third-party]. **Verify.** |
| Rate limits | 10/min free, 1,000/min paid on company enrich [official] | 5 req/s on trial, Mini and Starter; up to 100+ on higher plans [official] | Per plan, e.g. Free 50/min and 600/day; paid plans up to 1,000/min on enrichment [official] | Not documented [not verified] | Pro 50/min and 1,500/day; higher on other plans [third-party] |
| Sandbox | Free tier is the sandbox | Free trial | Free tier | None known | None known |

### Licensing and terms notes

| Provider | What we could verify | Concern for us |
| --- | --- | --- |
| PDL | Terms summarized from a partner's flow-down of PDL's terms: internal business purposes only, including segmentation and personalizing marketing campaigns; no resale or sublicensing; customer may retain data after termination but owns ongoing privacy workflows and opt-outs [third-party: [Primer data partner terms](https://www.sayprimer.com/third-party-terms), retrieved 2026-10-10]. PDL states its data sources are proprietary and public, with supplier due diligence, and that it has an opt-out process for API users [official: [data sources](https://docs.peopledatalabs.com/docs/data-sources)]. Its own terms page timed out, so the **primary terms were not read**. | "Internal use" is fine for Growviah's own prospecting. Serving multiple external clients from one account may need explicit permission. Caching is allowed in practice because retention after termination is described, but confirm in the contract. |
| Coresignal | Says it collects only publicly available data, with no fake accounts or login-protected areas, is an Ethical Web Data Collection Initiative member, and is GDPR and CCPA compliant [official: [docs](https://docs.coresignal.com/introduction/data-and-compliance), [statement](https://coresignal.com/statement-on-compliance/)]. The docs do not state license terms on storage, GDPR role, or customer deletion duties. | **Biggest legal-review item.** Public-web collection, widely understood to include LinkedIn-derived records, is the exact grey area Brief §4 warns about. Ask Coresignal directly in writing; do not assume. |
| Apollo | API terms: license is non-transferable and "solely for your internal business purposes"; you may not "integrate the APIs with your product or services" unless Apollo approves; may not use the API to replicate or compete with Apollo [official: [API terms](https://www.apollo.io/terms/api), retrieved 2026-10-10]. Apollo may also use customer data to grow its contributor database [third-party summary of terms]. | The "integrate with your product" clause is a direct conflict with building an adapter-based product unless approved in writing. Also check data-sharing clauses for client data. **Do not make Apollo the primary without written clarification.** |
| Crunchbase | Enterprise contract; license specifics [not verified] | Likely allows storage under contract, but price makes it a funding-only add-on candidate. |
| Lusha | Contact-oriented; terms [not verified] | Mainly a people-data tool; see section 2. |

### Regional coverage (Saudi Arabia and GCC)

We found **no vendor publishing Saudi coverage numbers** for company data.

- PDL: no Saudi or Middle East coverage figure found [not verified]. Global fill
  rates are public; Saudi-specific ones are not.
- Coresignal: no regional breakdown found [not verified].
- Apollo is described as US-centric and returned few results for Saudi CX
  roles in one third-party test [third-party:
  [Origami](https://origami.chat/blog/find-vp-customer-experience-saudi-ecommerce)].
- Arabic company names: no vendor documents Arabic-language data quality
  [not verified]. Plan for English or transliterated names and test matching.
- **Authoritative Saudi registry:** the Ministry of Commerce's **Wathq**
  platform (developer.wathq.sa) offers 24 APIs including commercial registration
  and company contracts, API-key auth, an Arabic and English interface, and
  subscription packages whose prices require a login [official, retrieved
  2026-10-10]. It does not give headcount or growth, but it gives legal name,
  CR number and status, which is valuable for disambiguation and as a trusted
  second source. Eligibility for a non-Saudi or agency user is [not verified].

### Suitability against the brief (company data)

Scale: Good, Partial, Weak, Unknown. This is based on documentation only; the
bake-off decides.

| Requirement | PDL | Coresignal | Apollo | Crunchbase | Wathq |
| --- | --- | --- | --- | --- | --- |
| Basic profile (§5) | Good | Good | Good | Good | Partial (legal data only) |
| Headcount by department | Partial (add-on) | Partial to good (unverified fields) | Unknown | Weak | Weak |
| 3/6/12 month growth | Good (Premium bundle) | Partial (derive from historical) | Unknown | Weak | Weak |
| Funding | Partial | Partial | Partial | Good | Weak |
| Job openings | Partial | Good (Jobs API) | Unknown | Weak | Weak |
| Leadership hires | Partial (Comprehensive bundle) | Unknown | Unknown | Weak | Weak |
| Saudi coverage | Unknown | Unknown | Weak to unknown | Unknown | Good (official registry) |
| License fit (store, derive, export) | Partial: confirm | Unknown: ask | Weak: product-integration clause | Unknown | Unknown |
| Cost transparency | Good | Good | Partial | Weak | Weak |
| Free trial for a bake-off | Yes | Yes | Yes | No | Unknown |

## 2. People and contact data

| | PDL (person) | Apollo | Cognism | Lusha | Coresignal (employee) |
| --- | --- | --- | --- | --- | --- |
| Access | REST [official] | REST, paid plans [third-party] | API as add-on to a platform contract [third-party] | REST, Pro plan and up [third-party] | REST [official] |
| Search by company and title | Yes (search and enrich) | Yes; people search consumes no credits [third-party] | Yes [third-party] | Prospecting API [third-party] | Yes, search preview 20 credits per page [official] |
| Name, title, profile URL | Yes | Yes | Yes | Yes | Yes (300+ fields) [official] |
| Email | Included in enrichment; verified level varies [not verified] | Credits per reveal; 1 credit demographics or email, +8 with mobile [third-party] | Yes, with verification claims [third-party] | Yes | Not a core email product [not verified] |
| Pricing | About $0.28/person credit on monthly plans, down to about $0.20 at volume [third-party]. **Verify.** | Account credit pool; API has no separate dollar price [third-party]. **Verify.** | Not public; reports of roughly $15,000/year plus per-user fees [third-party, unverified]. **Verify.** | Plans not fully verified; billed per row [third-party]. **Verify.** | Credits, see above [official] |
| Rate limits | 1,000/min paid on company; person limits [not verified] | See above | [not verified] | 50/min Pro, up to 300/min [third-party] | 5 to 100+ req/s [official] |
| Saudi and GCC | Unknown | Weak to unknown [third-party] | Claims growing UAE and Saudi coverage, with phone verification in UAE and Saudi Arabia; MEA dataset is "a fraction" of Europe [third-party: [SyncGTM](https://syncgtm.com/blog/best-phone-databases-middle-east-africa)] | Unknown | Unknown |
| Terms | Internal use; privacy workflows on customer [third-party] | Internal use; integration clause [official] | Do-not-call screening in 13 to 15 countries [third-party]; license [not verified] | [not verified] | [not verified] |

Notes:

- Cognism is the one vendor with a stated Middle East effort, but it is
  platform-and-seat based and quote-only, so it is a premium option to price,
  not a V1 default.
- **Wiza and similar tools** that market LinkedIn-based contact finding are not
  shortlisted for people data. We did not research them in depth and have not
  verified how they collect data (see excluded approaches).

### Personal data, GDPR and Saudi PDPL

Contact data about named people is personal data under GDPR and under the Saudi
Personal Data Protection Law (PDPL). We are not lawyers; points to take to one:

- PDPL Article 25, as summarized by law-firm commentary, bars using personal
  means of communication, including email, to send advertising material without
  prior consent, with limited exceptions [third-party: [Tamimi](https://www.tamimi.com/law-update/september-2021/articles/an-overview-of-saudi-arabias-new-personal-data-protection-law/),
  [Clyde & Co](https://www.clydeco.com/en/insights/2024/01/countdown-to-compliance-with-saudi-arabia-pdpl)].
  **Whether B2B cold email to a business address counts, and what the
  exceptions are, needs Saudi counsel.** A 2026 draft amendment to the
  implementing regulations was published by SDAIA [third-party: [Clyde & Co](https://www.clydeco.com/en/insights/2026/10/sdaia-publishes-draft-amendments-to-the-pdpl)]
  and the rules may be moving.
- PDPL allows processing for a controller's legitimate interests where the
  data subject's rights are not prejudiced, but not for sensitive data
  [third-party]. Legitimate interest and Article 25 interact in ways we cannot
  settle here.
- Cross-border transfer: sending Saudi residents' data to a US vendor or LLM
  needs a lawful transfer route such as the SDAIA standard contractual clauses
  [third-party]. This affects every vendor, including the LLM provider.
- GDPR applies if any target or contact is in the EU or UK. Vendors named above
  state GDPR programs; the responsibilities for deletion requests stay with us
  once data is stored.

Design consequences are in [adapter-requirements.md](adapter-requirements.md):
contact enrichment only for human-approved accounts (Brief §12), retention
limits on raw personal payloads, deletion on request, and no personal data sent
to logs.

## 3. Email enrichment

| | Hunter | Apollo | Prospeo | Findymail | Snov.io |
| --- | --- | --- | --- | --- | --- |
| Access | REST; key via query, `X-API-KEY` or Bearer header [official] | Credits on people enrichment [third-party] | REST [third-party] | REST [third-party] | REST [third-party] |
| Find by name and domain | Email Finder, Domain Search, Company Enrichment [official] | Yes | Yes | Yes | Yes |
| Built-in verification | Finder verifies results [official] | Status field [not verified] | Yes [third-party] | Yes, with a bounce guarantee [third-party] | Yes [third-party] |
| Free tier | 50 credits/month with full API access [official] | Free tier with API limits [official] | 75 credits/month [third-party] | None found | 50-credit trial [third-party] |
| Pricing | Starter $49/month for 2,000 credits; Growth $149 for 10,000; Scale $299 for 25,000; 1 credit per found email, 0.5 per verification [official, retrieved 2026-10-10] | Shared pool | From $39/month for 1,000 credits [third-party] | From $49/month for 1,000 credits [third-party] | From $30/month for 1,000 credits [third-party] |
| Rate limits | Finder and Domain Search 15 req/s and 500/min; Verifier 10 req/s and 300/min [official] | See above | [not verified] | [not verified] | [not verified] |
| Terms and provenance | Terms [not verified here] | Internal use and integration clause [official] | Known mainly as a LinkedIn-oriented finder; provenance [not verified] | Provenance [not verified] | Provenance [not verified] |
| GCC and Arabic names | [not verified] | [not verified] | [not verified] | [not verified] | [not verified] |

Hunter's pattern-based approach depends on the domain's published emails and
patterns, so Arabic-name transliteration (for example "Mohammed" versus
"Muhammad") affects hit rate. Nobody documents this; the bake-off must measure it.

## 4. Email verification

| | ZeroBounce | NeverBounce | Kickbox | Bouncer | Hunter Verifier |
| --- | --- | --- | --- | --- | --- |
| Access | REST: single, batch, credit-balance endpoints [official] | REST [third-party] | REST [third-party] | REST, sync single and async batch [third-party] | REST [official] |
| Result vocabulary | Status and sub_status codes [official, values not read] | valid, invalid, disposable, catch-all, unknown [third-party] | deliverable, undeliverable, risky, unknown, with reason codes [third-party] | Deliverable, risky, undeliverable, unknown plus toxicity score [third-party] | valid, invalid, accept_all, webmail, disposable, unknown [official] |
| Free tier | 100 credits/month, non-expiring [official] | Not confirmed | 100 free credits [third-party] | Not confirmed | Shares Hunter's 50 credits |
| Pricing | Pay as you go from $39 for 2,000; ONE subscription from $99/month; 1 credit per verification; unknowns cost 0 [official, retrieved 2026-10-10] | About $8 per 1,000 at small volume [third-party]. **Verify.** | From $5 per 500 [third-party]. **Verify.** | About $8 per 1,000, less at volume; unknown and duplicates not charged [third-party]. **Verify.** | 0.5 credit [official] |
| Rate limits | Not read | 10 concurrent bulk jobs and 50 job runs/day [third-party] | Not found | Reported up to 200,000/hour [third-party] | 10 req/s and 300/min [official] |
| Catch-all handling | Documented sub-statuses; quality unverified | Flags catch-all | Folded into risky | Marketed as a strength [third-party] | accept_all, cannot judge individual mailbox |
| Notes | Charges 0 for unknown, which fits our "unknown is not valid" rule | | | EU-based [third-party] | |

For Saudi and GCC corporate domains hosted on Google Workspace or Microsoft
365, catch-all and "unknown" results will be common whichever vendor is picked.
Our normalized enum (valid, invalid, risky, unknown, catch_all, from #84) keeps
those states distinct and never upgrades them.

## 5. LLM providers with structured outputs

| | Anthropic Claude | OpenAI | Google Gemini | Mistral |
| --- | --- | --- | --- | --- |
| Structured output | GA: `output_config.format` JSON schema and strict tool use; Pydantic parse helper in the Python SDK [official: [docs](https://platform.claude.com/docs/en/docs/build-with-claude/structured-outputs)] | GA: `json_schema` with `strict: true`; refusals reported separately [official: [docs](https://developers.openai.com/api/docs/guides/structured-outputs)] | JSON Schema via `response_format`, Pydantic in the Python SDK; a subset of JSON Schema [official: [docs](https://ai.google.dev/gemini-api/docs/structured-output)] | Structured output supported [third-party]; official page not reachable (404) |
| Schema limits | No recursion, no numeric or string-length constraints; our own Pydantic validation stays the source of truth | Subset of JSON Schema | Large or deep schemas may be rejected | [not verified] |
| Data use | Does not train on API inputs and outputs by default; 30-day deletion, zero-retention by agreement [official support pages via search] | Does not train on API data by default; abuse logs up to 30 days; zero-retention by approval [official via search] | Paid services do not use prompts for product improvement; free tier may [third-party] | EU hosting [third-party] |
| Pricing | Per-token; see the vendor pricing page. **Verify.** | Per-token. **Verify.** | Per-token, free tier. **Verify.** | Per-token. **Verify.** |
| Arabic | Not documented by vendors; test in bake-off [not verified] | Same | Same | Same |

All four fit ADR 0003 as the adapter's native mode, but per ADR 0003 we still
validate with Pydantic and retry once. Model names and prices change
frequently; pin them in integration settings, not in code. For Saudi data, the
cross-border transfer point above applies to every hosted LLM.

## 6. CRM and outreach

### GoHighLevel (HighLevel) API

| Item | Finding |
| --- | --- |
| Auth | API v2. OAuth 2.0 for Marketplace apps (access tokens last about 24 hours with a refresh token) and **Private Integration Tokens (PIT)**, which are static, scoped and non-refreshing [official: [PIT docs](https://marketplace.gohighlevel.com/docs/Authorization/PrivateIntegrationsToken.md)] |
| Token hygiene | Scopes are restrictable and editable; 90-day rotation recommended with a 7-day overlap; a token is shown once [official] |
| Scoping | A connection is scoped to one sub-account (location). Requests carry `locationId` and a `Version` header (`2021-07-28` for contacts) [official: [upsert docs](https://marketplace.gohighlevel.com/docs/2021-07-28/ghl/contacts/upsert-contact)] |
| Contact write | `POST /contacts/upsert` creates or updates, following the location's "Allow Duplicate Contact" setting; response says whether a contact was created [official] |
| Rate limits | 100 requests per 10 seconds and 200,000 per day, per app per location; headers `X-RateLimit-Max`, `X-RateLimit-Remaining`, `X-RateLimit-Limit-Daily`, `X-RateLimit-Daily-Remaining`, `X-RateLimit-Interval-Milliseconds` [official: [rate limits](https://marketplace.gohighlevel.com/docs/other/rate-limits)]. 429 behaviour is not documented on that page. |
| Sandbox | Sandbox accounts support PITs, limited to 25 requests per 10 seconds and 10,000 per day, may be reset or purged, and last at most 6 months [official: [sandbox PIT docs](https://marketplace.gohighlevel.com/docs/oauth/SandboxPIT.md)] |
| Cost | API access reportedly depends on plan (Starter $97, Unlimited $297 per month) [third-party]. Verify which plan the client uses. |
| Licensing and PII | Data is the client's own CRM data, so the license question is the client's agreement with HighLevel. We are a pusher, not a store. |

Implication for #85: idempotency cannot rely on a vendor idempotency key. Use
upsert plus our own record of what we pushed (contact ID, our external key),
and a dry-run mode. The dedupe behaviour depends on a location setting we do not
control, so the adapter should check it or document the requirement.

### Generic CRM and outreach alternatives (fallbacks)

| | HubSpot | Pipedrive | Instantly | Smartlead |
| --- | --- | --- | --- | --- |
| Role | CRM | CRM | Outreach | Outreach |
| Auth | Private app token, non-expiring [third-party] | API token or OAuth | Bearer API v2 key with scopes [third-party] | API key |
| Rate limits | Burst 100 per 10 s and 250,000/day on Free or Starter private apps [third-party] | 20 to 120 per 2 s by plan; token-based daily budget [third-party] | 100 req/s and 6,000/min per workspace [third-party] | 60/min per key on the standard tier [third-party] |
| Notes | Batch endpoints exist | Search has its own limit | Brief §21: we do not send; adapter only hands off | Same |

Brief §21 and ADR 0002 say the product hands data downstream and never sends
outreach on its own. Outreach platforms are therefore **export targets**, and
none is needed for V1 beyond a generic export and the GoHighLevel push.

## 7. Optional signal sources (job postings and news)

| | TheirStack | Coresignal Jobs | PDL job data | GDELT DOC 2.0 | NewsAPI.org |
| --- | --- | --- | --- | --- | --- |
| Covers | Job postings and technographics [third-party] | Job postings, 1 credit each [official] | Postings from career pages [official] | Global news index [third-party] | News headlines [third-party] |
| Access | REST [third-party] | REST | REST | Open API, no key [third-party] | REST |
| Cost | Free tier 200 API credits/month; subscriptions from $59/month [third-party]. **Verify.** | Within Coresignal plan | Within PDL plan | Free, with citation required [third-party] | Dev plan is free but not for production; Business $449/month [third-party]. **Verify.** |
| Fit | Hiring signals (§5 job openings, §7 hiring triggers) | Same | Same | Expansion and partnership news, with noisy relevance and Arabic coverage unknown | Likely poor Saudi coverage [not verified]; not for production on free plan |
| Terms | [not verified] | See Coresignal | See PDL | Unrestricted use with citation [third-party] | Commercial use needs paid plan |

Saudi job boards without an API are not shortlisted (scraping risk). Adzuna was
considered; we found no confirmation that it covers Saudi Arabia, so it is
[not verified] and not shortlisted.

For news evidence, keep the original article URL and date, since Brief §5
requires source and date for important evidence.

## Recommendation

This is a proposal for the owner to accept, change or reject. The first step for
everything below is a **bake-off on free trials**, not a purchase.

### First licensed company-data provider

**Proposed: People Data Labs, with Coresignal as the challenger in the
bake-off.** Wathq is proposed as a free-to-evaluate second source for Saudi
legal identity, not as the main provider.

Why PDL first:

- Published free tier and published rate limits; billing per successful match
  only; clear field names for 3, 6 and 12 month growth.
- Documented department headcount and role growth fields, with the honest
  caveat that these are a **Premium add-on** (so the free tier may not show them;
  cost to be confirmed).
- Terms we could see allow internal marketing use and describe retention after
  termination.
- Provider-neutral: our normalized model works with its raw `company` record.

Why not sign yet:

- PDL's Saudi coverage is unknown and could be thin. The bake-off decides.
- We did not read PDL's own terms page (timeout); read it before committing.
- The Premium bundle pricing is not public to us.

Why Coresignal is the challenger: documented Historical Headcount and Jobs
endpoints with published credit prices and a 7-day, 2,000-credit trial. Its
legal posture on public-web collection needs a written answer first (Brief §4).

Why not Apollo as primary: its API terms contain an "integrate with your
product or services" restriction. It may still serve as a people-data fallback if
Apollo approves our use in writing. Crunchbase is a funding add-on if funding
signals matter more than cost.

### First people and email stack

| Layer | Primary (proposed) | Fallback |
| --- | --- | --- |
| People data | PDL person search (same account and terms as company data) | Coresignal employee API, or Apollo with written permission, or Cognism if Saudi coverage proves decisive and budget exists |
| Email enrichment | Hunter (published prices, free 50 credits, full API on free plan) | PDL email fields, or Findymail or Snov.io after a provenance check |
| Email verification | ZeroBounce (published prices, 100 free credits a month, zero charge for unknown) | Bouncer or Kickbox |
| LLM | Anthropic Claude (native structured outputs fit ADR 0003) | OpenAI or Gemini through the same adapter |
| CRM | GoHighLevel via Private Integration Token | HubSpot or generic CSV export |
| Outreach | None for V1; export only (Brief §21) | n/a |
| Job signals | PDL or Coresignal jobs data, plus TheirStack as an option | n/a |
| News | GDELT (free, cite source) | NewsAPI Business plan if budget allows |

The LLM row is a proposal on the strength of documentation fit with ADR 0003.
Arabic quality, price and data-handling terms for Saudi data must be compared
in the bake-off on our own test prompts.

### Decision criteria

Rank candidates on these, in this order (the order is a proposal too).

1. **Licence fit:** internal use, caching, derived AI processing, CRM export, no
   re-sale; no conflict with a multi-client agency model.
2. **Collection legitimacy** (Brief §4): vendor can describe its sources and
   states no unauthorized scraping.
3. **Coverage on our real targets** (Saudi companies): measured, not claimed.
4. **Brief field coverage:** headcount by department and 3/6/12 month growth.
5. **Total cost per researched account** at 50, 100 and 500 companies.
6. **Operational fit:** published rate limits, clear errors, a sandbox or free
   tier, stable docs.
7. **Swappability:** field mapping to our normalized models without loss.

### How to run a bake-off

Goal: choose with data. Run it on free tiers and trials; do not buy plans for it
unless the owner decides to.

1. **Write down the budget and time-box** (suggest one week and a hard credit
   cap per vendor). Create accounts in the project owner's name; keep keys in
   the secrets store, never in the repo.
2. **Build the test list (about 40 companies)** in four groups. Record the
   ground truth from each company's own website or filings first, in a sheet
   kept outside the repo if it contains people.
   - **Saudi financial and fintech (Brief §3, §10):** tiqmo and 4 to 6 peers
     such as other Riyadh fintechs.
   - **SkyLight's market (Brief §3):** 10 Saudi SaaS, technology, accounting
     and financial-services companies with 10 to 500 employees.
   - **Large and small control group:** 5 well-known GCC companies and 5
     companies under 20 staff, to check size bias.
   - **Hard cases:** 5 companies with Arabic-only names or sites, 3 with a
     recent funding round or leadership change, 3 that do not exist (to test
     "not found" and false-match behaviour).
3. **Run each candidate through the same inputs** (domain first, then name only)
   using the minimum number of calls. Save raw responses outside the repo and
   record credits spent.
4. **Score against acceptance metrics** (suggested thresholds, adjust once the
   owner sets the bar):
   - Match rate: at least 80% of real test companies found with the correct
     entity; false-match rate at most 2%.
   - Field fill rate for Brief §5: industry, description, HQ and employee count
     at least 80%; department headcount for sales, BD, marketing and commercial
     at least 50%; 3/6/12 month growth at least 50%.
   - Accuracy: employee count within 25% of a hand-checked figure for at least
     70% of companies; HQ city correct for at least 90%.
   - Freshness: `retrieved`, `updated` or `last seen` dates present and under 12
     months old for at least 80% of records.
   - People: at least 70% of companies return one plausible decision maker from
     the campaign's preferred titles; named person verified to still work there
     for at least 85% (hand-check 20).
   - Email: for 20 approved-style contacts, enrichment hit rate and the share of
     enriched emails the verifier calls valid; measure the bounce expectation,
     not a real send.
   - Verifier: run known-good, known-bad and catch-all addresses (the team's
     own, never third parties); status must agree with expectation at least 90%
     and never mark unknown as valid.
   - Cost: credits per fully researched company, and projected cost per 500.
   - Reliability: p95 latency, 429 behaviour and error shape under 20 parallel
     calls (respect published limits).
   - Provenance: every field traceable to a source and date; vendor can state
     source type.
5. **Paper check before any payment:** ask each finalist, in writing, the
   questions in the next section; file the answers with the ADR.
6. **Decide and record** the primary and fallback per category in ADR 0012,
   including what was rejected and why.

### Questions to put to each vendor in writing

- May we store responses and keep them after the subscription ends?
- May we run AI processing on the data and store the results?
- May we export records to our client's CRM, and use them on behalf of
  multiple clients? Is the licence for our own use only?
- Are there caching limits or required refresh rules?
- How was each data family collected? Does it include LinkedIn-derived data and
  on what basis?
- What are your and our roles under GDPR and the Saudi PDPL? Do you provide a
  data processing agreement and standard contractual clauses? Where is data
  hosted?
- What is your deletion and opt-out process, and what must we do when you tell
  us someone opted out?
- What is covered for Saudi Arabia and GCC, and what fill rates do you see
  there? Arabic names and sites?
- Rate limits, burst rules and credit rules for failed or empty calls.
- SLA, deprecation notice period and price-change terms.

## What could not be verified

- PDL's own terms and data-license pages (the fetch timed out; terms text came
  from a partner's flow-down) and PDL pricing from its own page.
- Saudi and GCC coverage for every vendor, and Arabic data quality for every
  vendor and every LLM.
- Coresignal field names for department headcount and 3/6/12 growth, and its
  license terms on storage and GDPR roles.
- Apollo department headcount, and Apollo's file data handling beyond the API
  terms.
- Cognism, Lusha, Crunchbase, Prospeo, Findymail, Snov.io, NeverBounce,
  Kickbox and Bouncer terms and rate limits from official pages; most figures
  here are from third-party summaries.
- Whether Wathq is open to us, and its pricing.
- GoHighLevel 429 behaviour, per-plan API entitlement, and whether the
  client's account has Private Integrations enabled.
- Current per-token LLM prices and model names (not recorded on purpose).
- Whether PDPL Article 25 consent applies to B2B email to business addresses.

## Sources

All retrieved 2026-10-10. Official pages are marked in the tables; third-party
pages are summaries and should not be relied on for a contract.

- PDL: [company enrichment](https://docs.peopledatalabs.com/docs/reference-company-enrichment-api),
  [company data overview](https://docs.peopledatalabs.com/docs/company-data-overview),
  [data sources](https://docs.peopledatalabs.com/docs/data-sources),
  [feedback on role-count access](https://feedback.peopledatalabs.com/feature-requests/p/enable-employee-count-by-role-and-employee-growth-rate-12-month-by-role-for-self),
  [pricing summary (third-party)](https://pipeline.zoominfo.com/sales/people-data-labs-pricing)
- Coresignal: [pricing](https://docs.coresignal.com/pricing),
  [data and compliance](https://docs.coresignal.com/introduction/data-and-compliance),
  [statement](https://coresignal.com/statement-on-compliance/)
- Apollo: [API terms](https://www.apollo.io/terms/api),
  [rate limits](https://docs.apollo.io/reference/rate-limits.md)
- Hunter: [API v2](https://hunter.io/api-documentation/v2), [pricing](https://hunter.io/pricing)
- ZeroBounce: [docs](https://www.zerobounce.net/docs/email-validation-api-quickstart/),
  [pricing](https://www.zerobounce.net/pricing)
- LLM docs: [Claude](https://platform.claude.com/docs/en/docs/build-with-claude/structured-outputs),
  [OpenAI](https://developers.openai.com/api/docs/guides/structured-outputs),
  [Gemini](https://ai.google.dev/gemini-api/docs/structured-output)
- GoHighLevel: [rate limits](https://marketplace.gohighlevel.com/docs/other/rate-limits),
  [upsert contact](https://marketplace.gohighlevel.com/docs/2021-07-28/ghl/contacts/upsert-contact),
  [private integrations](https://marketplace.gohighlevel.com/docs/Authorization/PrivateIntegrationsToken.md),
  [sandbox PIT](https://marketplace.gohighlevel.com/docs/oauth/SandboxPIT.md)
- Wathq: [developer portal](https://developer.wathq.sa)
- Others (third-party summaries): Crunchbase, Cognism, Lusha, Prospeo,
  Findymail, Snov.io, NeverBounce, Kickbox, Bouncer, TheirStack, GDELT,
  NewsAPI, HubSpot, Pipedrive, Instantly, Smartlead and PDPL commentary, found
  through web search on 2026-10-10 and linked inline where they matter.
