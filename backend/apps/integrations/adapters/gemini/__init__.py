"""Google Gemini ``generateContent`` adapter."""

from .adapter import (
    API_HOST,
    DEFAULT_THINKING_HEADROOM,
    OUTPUT_TOKEN_CAP,
    STYLE_JSON_SCHEMA,
    STYLE_RESPONSE_FORMAT,
    GeminiAdapter,
    prepare_schema,
)

__all__ = [
    "API_HOST",
    "DEFAULT_THINKING_HEADROOM",
    "OUTPUT_TOKEN_CAP",
    "STYLE_JSON_SCHEMA",
    "STYLE_RESPONSE_FORMAT",
    "GeminiAdapter",
    "prepare_schema",
]
