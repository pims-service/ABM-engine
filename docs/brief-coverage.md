# V1 brief: what is built and what is not

Checked against `Growviah ABM Engine - V1 Development Brief` section by section.
Status: **Done** (built and covered by automated tests), **Partly** (works, with a stated gap),
**Needs a provider** (the code is ready for an adapter, but nothing is connected), **Not built**.

None of the live outside calls (Anthropic, GoHighLevel) has been run with a real key yet. They are
tested against fakes and, for GoHighLevel, checked against the official API documentation.

| Brief section | Status | Notes |
|---|---|---|
| 1 Objective, 30 to 60 second review | Done | Research card plus the keyboard review queue (`/companies/review`). |
| 2 ICP fit and trigger kept apart | Done | Separate judgements and fields. A strong fit with no trigger stays eligible. |
| 3 Campaign setup | Done | All fields, versioned profiles, guidance and presets in the form. Several clients and campaigns. |
| 4A Manual entry | Done | Name, website and profile URL. Duplicate warnings. |
| 4B CSV upload | Done | 500+ rows, preview, skipped-row report, progress, cancel. |
| 4C Provider import | Needs a provider | The adapter framework, the queue and a Find companies search screen exist and work end to end with the offline test provider. No real company-data provider is connected, so there is nothing real to search yet. |
| 5 Company information | Partly | From the company website through the AI, each fact with its source (Analyze Company), and from a company-data provider through the research run (`POST /companies/{id}/research/`), which stores a snapshot and lists the fields the provider did not return. The provider part is tested only with the offline fake. Where sources disagree, the merged profile shows one value per field by source kind (`RESEARCH_SOURCE_PRECEDENCE`) with the other values as alternatives. |
| 5 Commercial structure and growth | Partly | Entered by hand on the card, or collected by the research run from a provider: sales, BD, marketing and commercial and partnerships headcounts, and 3, 6 and 12 month growth from the growth module (the provider's own percentages are only a fallback). Job titles go into departments through an editable English and Arabic rules file, and unclear titles are never counted. Needs a real company-data provider to run on real data. |
| 5 Signals | Partly | Found on the company's own pages, each needing a date and a quote that really appears on the page. The research run adds provider signals, funding and hiring by department, and news items tagged by keyword; a second source for the same event keeps both sources on one signal, and an undated event is counted but never stored. Funding, job and news feeds still need real providers: only offline fakes exist. |
| 5 Source and date on evidence | Done | Enforced: no source or no date means the signal is dropped. |
| 6 ICP qualification | Done | Structured output, hard rules after the model, reasons and concerns, no numeric score. |
| 7 Trigger detection | Partly | Yes or no with type, evidence, date and source; several triggers; each type expires after a fixed window. The windows are the same for every campaign (changing them per campaign is not built). |
| 8 Decision-maker finder | Done with SignalHire | Find people by the campaign's titles through SignalHire, or add them by hand; ranking by size and titles with reasons and a primary and secondary target. SignalHire gives no LinkedIn URL in its search results. Not tried with a live key. |
| 9 Outreach angle | Done | Stored apart from messages. |
| 10 Research card | Done | |
| 11 Human approval | Done | Add, hold or skip; mandatory; stored apart from the AI's advice; nothing starts by itself. |
| 12 Approved workflow: find contact, enrich, verify | Done with SignalHire and ZeroBounce | Find email (SignalHire, approved accounts only), Verify email (ZeroBounce), and a Next steps list on the card that ticks off contact, email, verify, messages, approve, export. Both also run for many people at once as background jobs (`enrich-contacts`), and the CRM push has a background version (`queue-push-to-crm`), each with a button on the Companies page. Neither provider has been tried with a live key. |
| 12 Messages | Done | LinkedIn message, email with subject and follow-up, campaign languages, Saudi Arabic. Drafts that invent facts or numbers are refused. Human approval before export. |
| 12 Export or push | Done | CSV export; GoHighLevel push (not tried with a live account). No separate outreach-platform adapter. |
| 13 Dashboard columns and filters | Done | Every column and filter in the brief, plus quick filters with counts. |
| 14 Account detail | Done | Overview, ICP, signals, commercial team, people, outreach, activity, research history. |
| 15 Explainable AI | Done | Reasons are written out for fit, trigger, buyer and angle. |
| 16 Feedback and learning | Done | All flags, stored on the timeline, a report of agreement with the AI, and a CSV for prompt and rule improvements. |
| 17 Integrations, replaceable | Partly | Category catalogue, encrypted connections, test button, metering and budget. Real adapters: Anthropic, OpenAI and Gemini (AI), SignalHire (people and email), Hunter (email), ZeroBounce (verification) and GoHighLevel (CRM). Still missing: a company-data provider, a news source, an outreach platform and storage. |
| 18 Architecture | Done | Next.js and Django REST on PostgreSQL, structured LLM output, background workers. Authentication is Django JWT instead of Supabase. |
| 19 Data objects and history | Done | Every object in the list. Research, assessments, recommendations, decisions and messages are never overwritten. Every source has a name and a date, evidence is listed per company (`GET /companies/{id}/evidence/`), and any two research snapshots can be compared. `RESEARCH_HISTORY_DAYS` limits how much history is listed; nothing is deleted. |
| 20 User flow | Done | Every step, including the steps after Add, with the Next steps list. |
| 21 Out of scope | Respected | No CRM, sending, LinkedIn automation, dialer, analytics suite or website visitor tracking. |
| 22 Success criteria | Partly | Bulk analysis, separate fit and trigger, visible evidence, ranked buyers, grounded angles, quick decisions, export and no invented facts are in. "Provider parts replaceable" is true of the design and of seven adapters, but there is no company-data provider yet. |

## Also built beyond the brief
Team roles and passwords, monthly AI budget and usage page, import history, archive and restore,
bulk decisions, recovery when a worker dies, and an AI-versus-human agreement report.

## What is left
1. **Still needs a real provider:** company data (automatic headcount by department, growth, funding
   and job signals on real companies) and a news source. The collectors, the research run and the
   endpoints are built and tested against the offline fakes; connecting a real provider means
   writing its adapter. SignalHire's company API only returns basic company details and is off by
   default. Mawsool has no adapter until its API documentation is available.
2. **Not built:** trigger windows per campaign, deleting old research (the history setting only
   hides it), news tagging by the AI model (it is keyword-based), a golden evaluation set for the
   prompts, a model tie-break step, an outreach platform and storage adapter, and hardening and
   launch work (load test at 500 companies, security and privacy reviews, a deployment pipeline
   with staging).
3. **To prove:** run a real analysis with an Anthropic key and a real push to GoHighLevel.
