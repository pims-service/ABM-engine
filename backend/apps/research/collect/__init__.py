"""The research collectors: company profile, signals and news, and the run that orders them
(issues #91, #93, #96, #98, #99). See docs/research-engine.md.

Every collector writes through the same services as the rest of the app: evidence through
``apps.research.evidence.record_evidence``, snapshots through ``add_research_snapshot`` and
signals through ``create_signal`` / ``supersede_signal``. Nothing here fetches a LinkedIn page.
"""
