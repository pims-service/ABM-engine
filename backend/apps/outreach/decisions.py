"""Adapter to the human decision (ADR 0007): outreach starts only after a human said ``add``.

``HumanDecision`` is owned by issue #42 (``apps.research``). To keep this app independent of the
order the two issues merge, the model is imported lazily here:

* If ``apps.research.models.HumanDecision`` does not exist (yet), ``latest_human_decision``
  returns ``None``, which services treat as "no decision" and so refuse to create or approve
  messages (fails closed, never open).
* Once #42 is merged nothing needs to change: the adapter finds the model and reads the latest
  decision (``latest_for`` on its manager if it has one, otherwise latest ``decided_at``,
  ties by ``created_at``/``id``). Its ``decision`` field holds ``add``, ``hold`` or ``skip``.

Services accept a ``decision_lookup`` argument (a ``Callable[[Company], str | None]``) so tests and
other callers can inject their own lookup.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from apps.companies.models import Company

ADD = "add"

DecisionLookup = Callable[[Company], "str | None"]


def _human_decision_model() -> Any | None:
    try:
        from apps.research import models as research_models
    except ImportError:  # pragma: no cover - the research app always exists
        return None
    return getattr(research_models, "HumanDecision", None)


def latest_human_decision(company: Company) -> str | None:
    """The company's latest human decision (``"add"``, ``"hold"``, ``"skip"``), or ``None``.

    ``None`` means no decision was made, or the ``HumanDecision`` model is not available yet.
    """
    model = _human_decision_model()
    if model is None:
        return None
    manager = model.objects
    if hasattr(manager, "latest_for"):
        row = manager.latest_for(company)
    else:
        row = manager.filter(company=company).order_by("-decided_at", "-created_at", "-id").first()
    if row is None:
        return None
    return str(row.decision)
