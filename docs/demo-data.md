# Demo data

`python manage.py seed_demo_data --yes` fills an empty install with a realistic demo, for showing
the product. **Everything in it is invented**: the companies, the people, the news and the numbers.
No real business is named or described, the publications are made up, and the AI parts carry the
model name `demo-seed`. It never calls an AI provider or any outside service.

## What it creates

| What | How many |
| --- | --- |
| Clients | 2: SkyLight (a lead generation agency) and Nawa Cloud (managed security for healthcare) |
| Campaigns | 3, active, with full ICP rules and outreach languages: KSA Financial Services & SaaS, UAE Logistics & Freight, GCC Healthcare IT |
| Accounts | 29, in every state: waiting for analysis (5), analysed and waiting for review, ADD (8), HOLD (3), SKIP (4) |
| Research | website research, the commercial team (sales, BD, marketing, commercial) and 3, 6 and 12 month growth worked out by the growth module, with a different trend per company (one declining) |
| Signals | dated, with a source link: funding, hiring, new product, new office, leadership, partnership; some accounts have none (a strong fit does not need a trigger) |
| People | ranked buyers with a primary and secondary target, emails with their verified or unverified status |
| Outreach | angles, 8 messages (4 approved, 4 draft, one in Arabic), and a timeline of contacted, replied and meeting booked |
| Team | three demo members (a manager and two reviewers) who made the decisions; they have no password |

It is written through the same services the app uses, so each account is a real record with its
history, not a picture of one.

## Using it

```bash
python manage.py seed_demo_data --yes
```

It refuses to run when a demo client already exists, so it adds the demo once. It needs an
administrator to own the data. On the production server, run it inside the api container (take a
backup first, see [production](operations/production.md)).

## Removing it

The app's records are append-only, so the demo cannot be deleted row by row. Restore a backup taken
before it ran (`restore.sh`), or reset a development database (`reset_local_db`).

## Be clear that it is a demo

The data is realistic on purpose, so a demo does not look empty, but it is invented. Do not present
it as real client data or real market facts.
