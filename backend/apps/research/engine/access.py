"""The engine's view of the client's LLM connection (see ``apps.integrations.llm_connection``)."""

from __future__ import annotations

from apps.integrations.llm_connection import NOT_CONFIGURED, get_llm_adapter
from apps.integrations.llm_connection import LLMUnavailable as AnalysisUnavailable

__all__ = ["NOT_CONFIGURED", "AnalysisUnavailable", "get_llm_adapter"]
