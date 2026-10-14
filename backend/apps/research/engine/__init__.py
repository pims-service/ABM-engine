"""The qualification engine (Brief sections 5 to 7 and 9).

``run_analysis`` reads a company's public website, extracts facts and dated signals with an LLM
(every signal must quote the page), judges ICP fit against the campaign rules, and records:

* a ``CompanyResearch`` snapshot (facts found on the pages; everything else stays null),
* ``Signal`` rows (a source and an event date each, with a freshness window),
* an ``ICPAssessment`` (fit only, no trigger input) and an ``AIRecommendation``.

Nothing here decides for the person: the human decision is a separate record. Nothing is
invented: a fact the pages do not state is stored as null, and a signal that cannot quote its
page or carries no date is dropped. See docs/research/qualification-engine.md.
"""

from .access import AnalysisUnavailable, get_llm_adapter
from .pipeline import AnalysisFailed, AnalysisOutcome, run_analysis

__all__ = [
    "AnalysisFailed",
    "AnalysisOutcome",
    "AnalysisUnavailable",
    "get_llm_adapter",
    "run_analysis",
]
