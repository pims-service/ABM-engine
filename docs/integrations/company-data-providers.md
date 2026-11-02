# Company data providers

Which providers the engine can use to fill in a company's profile, headcount by department,
growth, funding and open jobs, why these three, and what is not verified. The collectors that use
them are described in [the research engine](../research/research-engine.md); the adapter
framework is in [the integration framework](framework.md), and the earlier provider shortlist is in
[provider-shortlist.md](provider-shortlist.md).

## Connect one

Settings > Integrations > Company data, then **Apollo** or **People Data Labs**, paste the API
key. Hunter can also serve as a company-data provider (see below) but has no tile, so it is not
chosen by accident: create a connection with category *Company data* and provider `hunter`. A
client uses its oldest active company-data connection. Then **Collect provider data** on a
company (History tab) starts a research run.

**Find companies** (the company search and import screen) lists the search-capable providers a
client has connected (Apollo, People Data Labs) next to the offline test provider, for the
selected campaign only. A search uses that client's own key, is metered like every provider call
and counts against the connection's budget; another client's connection is never used. Hunter has
no company search, so it is not listed there.

## The three adapters

| | Apollo | People Data Labs | Hunter |
| --- | --- | --- | --- |
| Company profile | yes | yes | yes (by domain only) |
| Search by country, size, keyword | yes (paid plan) | yes | no |
| Headcount by department | sales, marketing, BD | sales, marketing, partnerships | no |
| Growth | 6 and 12 months, given by Apollo | 3, 6 and 12 months, worked out from its monthly headcount | no |
| Funding | total, last round, amount | total, last round, amount, currency | total only, no currency |
| Open job postings | yes, a list with dates | counts only, so not offered | no |
| Cost model | 1 credit per company, search page or job page | 1 credit per match (a miss is free); search per record | not documented |
| Rate limit | about 600 a hour (plan dependent) | 10 a minute free, 1,000 paid | 500 a minute |
| Connection test | one enrichment call (1 credit) | looks up a company that does not exist: free | free account call |

All three are licensed APIs. Nothing here scrapes LinkedIn; a LinkedIn company URL is only an
identifier that is stored and sent to the provider as a lookup key.

## What was checked, and what was not

The adapters were written from each provider's own API documentation on 2026-11-01, and the
module docstrings list every endpoint and field used. **No API key was available, so none of them
has been run against the real service.** They are tested against fake transports built from the
documented responses. Before relying on one, connect a real key, press **Test connection** and run
one company. Known unverified points:

* Apollo: what it answers for an unknown company (an empty result, `404` and `422` are all handled);
  whether keyword tags match industry names the way you expect; the credit cost of a failed lookup.
  It has no free health endpoint, so its connection test costs one credit.
* People Data Labs: the exact search field names for country and industry (taken from the example
  record), and role names beyond the documented example. Growth is computed here from the monthly
  series, because the provider gives none.
* Hunter: the credit cost and the unit of `raised`, which is why funding carries no currency.

## Market research behind the choice

Comparison guides written by vendors rank themselves first, so they were used to find candidates,
and the API documentation decided what could be built.

| Provider | Verdict |
| --- | --- |
| [People Data Labs](https://docs.peopledatalabs.com/) | Built. Company record with headcount by role, a monthly headcount series, funding rounds and job posting counts. Pay per match. |
| [Apollo](https://docs.apollo.io/) | Built. Enrichment with department headcount and growth, job postings, search. Search needs a paid plan. |
| [Hunter](https://hunter.io/api-documentation/v2#company-enrichment) | Built, small. Useful when a Hunter key already exists for emails. Firmographics only. |
| [Coresignal](https://coresignal.com/) | Not built. Strong on historical headcount and job postings, but its public documentation gave no endpoint details to build from, and the headcount history is on higher tiers. A good next adapter once an account and its API reference are at hand. |
| Crunchbase | Not built. The strongest source for funding rounds, but access is a paid licence and gives nothing on headcount or jobs. |
| Proxycurl | Ruled out. It scraped LinkedIn and shut down in July 2025 after LinkedIn sued it. |
| ZoomInfo, Cognism, Lusha | Not built. Enterprise contracts and sales-led access; revisit if a client already has one. |
| Wathq (Saudi commercial registry) | Not built. Useful for the legal name and status of Saudi companies; needs its own API access. |

Nothing in the research addressed data residency or Saudi and UAE data protection law for any
vendor. Check that with each vendor before using real data from a client's market.

## Adding another provider

Follow `apps/integrations/adapters/apollo/adapter.py`: extend `CompanyDataAdapter`, declare only
the features the provider really has, give it an injectable transport, add its class to
`company_data_connection.py`, add a tile in `frontend/src/features/integrations/presets.ts`, and
test it with `CompanyDataContract` in `tests/test_company_data_adapters.py`.
