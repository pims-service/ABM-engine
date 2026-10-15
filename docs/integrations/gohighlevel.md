# GoHighLevel (CRM)

The last step of the approved workflow (Brief section 12): after you approve a message, push the
person to the client's CRM. Code: `backend/apps/integrations/adapters/gohighlevel/`,
`backend/apps/integrations/crm_connection.py` and `backend/apps/outreach/crm_push.py`.

## Connect it

Settings > Integrations > Add connection: category **CRM**, provider **gohighlevel**.

- Config: `{"location_id": "<your HighLevel location id>"}`. Optional: `"tag"` (default
  "ABM Engine") and `"source"` (default "ABM Engine").
- Credential `api_key`: a **Private Integration** token created in HighLevel with the contact
  permissions (create and edit contacts, notes and tags; location read for the connection test).

"Test connection" reads the location (`GET /locations/{id}`) to show the token and the id fit.

## What a push does

`POST /companies/{id}/push-to-crm/` (or "Send to GoHighLevel" on the account card, or "Push to
CRM" on the companies page for every ready account, up to 100 at a time):

1. Needs: a human ADD decision, a primary person **with an email address**, and at least one
   approved message. Otherwise it says what is missing and sends nothing.
2. Upserts the contact (`POST /contacts/upsert`: name, email, company name, source). HighLevel
   matches an existing contact by the location's "Allow Duplicate Contact" setting.
3. Adds the tag (`POST /contacts/{id}/tags`). Tags are added, never replaced.
4. Adds one note (`POST /contacts/{id}/notes`) with the title, the ICP fit, the AI recommendation,
   the triggers, the angle and the approved messages. The note is added only when the contact was
   just created, so pushing again never piles up notes (the "refresh note" option adds it again).
5. Marks the approved messages exported (logged on the account timeline).

Every call is recorded as a provider call and is subject to the connection's budget.

## Checked and not checked

The endpoints, headers and fields above were read from HighLevel's API reference (version
`2021-07-28`). It has not been run against a live account: the first push may reveal a missing
permission or a location setting (for example a location that needs a phone number).
