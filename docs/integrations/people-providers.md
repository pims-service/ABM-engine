# People and email providers

## SignalHire (built)

Settings, Integrations: add one connection under **People data** and one under **Email
enrichment**, both with provider `signalhire` and your SignalHire API key (no other settings).
"Test connection" reads your credit balance (`GET /credits`), which costs nothing.

* **Find people** (company card, People): searches SignalHire for the campaign's preferred buyer
  titles at the company and saves the matches as contacts with SignalHire as the source. People
  already listed are not added twice. Only profiles whose experience names the company are kept,
  because SignalHire matches company names as text. Search costs no credits; it uses SignalHire's
  daily search quota (shared with their website).
* **Find email** (per person): only for a company you decided ADD on, because each found email
  costs one credit. A found email is saved as *unverified* (a finder is not a verifier); a miss is
  saved as *not found*. It uses the person's LinkedIn URL when there is one, otherwise the
  SignalHire profile id saved when the person was found.
* Every call is metered and checked against the connection's budget. Out of credits or quota shows
  a plain message; rate limits are retried later by the job framework.

Not verified against a live account: no API key was available when this was built, so it was
checked against SignalHire's documentation and fake responses only. SignalHire's synchronous
reveal mode (used here, no public callback URL needed) has less current data and lower contact
coverage than their callback mode. Check coverage on a few Saudi and Gulf companies first.

## Mawsool (placeholder)

Selectable in the same categories. You can save a connection, but there is no adapter: Mawsool
publishes no API documentation we could find. When you have their docs, an adapter follows the
SignalHire pattern (`apps/integrations/adapters/signalhire`, `apps/integrations/people_connection.py`).
Until then, add contacts by hand or with SignalHire.
