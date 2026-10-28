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

## ZeroBounce (built): verify an email

Settings, Integrations: add a connection under **Email verification**, provider `zerobounce`, with
your API key (optional setting `region`: `default`, `us` or `eu`). "Test connection" asks for your
credit balance, which costs nothing.

* **Verify email** (per person, approved accounts only): ZeroBounce answers valid, invalid,
  catch-all, unknown, spam trap, abuse or do-not-mail. Only a clean *valid* marks the email
  verified; invalid marks it invalid; everything uncertain (risky, catch-all, unknown) leaves it
  unverified. An unknown result costs no credit.
* The key is sent in the request body, never in the URL.

Not verified against a live account.

## Many people at once: the background workers

Find email and Verify email also run as background jobs, so a slow, rate-limited or empty provider
never holds up a web request. `POST /campaigns/{id}/accounts/enrich-contacts/` takes a `step`
(`find_emails`, `verify_emails` or `find_and_verify`) and optionally `company_ids`, and answers at
once with how many people were queued, were already waiting, or were not ready.

* **Who:** the primary and secondary people of accounts decided ADD, at most 300 per request.
  Other people found by the search are never looked up in bulk.
* **Checked twice:** when queued and again just before each call, because a decision, an email or
  an archive can change while a job waits. A person who no longer qualifies is skipped, with the
  reason kept on the job item. An email that is already verified or invalid is not checked again.
* **Same rules as the buttons:** every call is metered and counted against the connection's monthly
  budget. A rate limit or provider outage re-queues the job after the wait the provider asked for;
  a rejected key, a missing configuration or an used-up quota ends the job at once without
  touching anyone.
* **Find then verify:** `find_and_verify` queues a verify job for each email the find job
  reveals, and verifies at once the people who already have an unverified email.
* **Where it lives:** `apps/companies/enrichment.py` (who and queueing) and the tasks
  `find_contact_emails` and `verify_contact_emails` in `apps/companies/tasks.py`, built on
  `provider_task` (`docs/integrations/resilience.md`). Progress is on the `Job` rows (type
  `find_contact_emails` or `verify_contact_emails`, one item per person).

## Mawsool (placeholder)

Selectable in the same categories. You can save a connection, but there is no adapter: Mawsool
publishes no API documentation we could find. When you have their docs, an adapter follows the
SignalHire pattern (`apps/integrations/adapters/signalhire`, `apps/integrations/people_connection.py`).
Until then, add contacts by hand or with SignalHire.
