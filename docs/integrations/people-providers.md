# People and email providers: SignalHire and Mawsool (placeholders)

Both names are selectable under Settings, Integrations, in the **People data** and **Email
enrichment** categories. You can save a connection with its API key (stored encrypted). Test
connection answers "No test available for this provider yet", never a pretend success.

Nothing is looked up yet: the adapters are not built. When one is, it registers a tester with
`register_tester` and an adapter in the registry (see `docs/integrations/` and the GoHighLevel
adapter for the pattern), and the contact lookup and email steps of the approved-account workflow
start using it. Until then, add contacts by hand on the company card.
