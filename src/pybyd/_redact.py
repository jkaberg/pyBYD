"""Helpers for safe debug logging.

pyBYD frequently deals with secrets (passwords, tokens) and encrypted blobs.
This module provides a small utility to redact sensitive fields before
emitting DEBUG logs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_SENSITIVE_VALUE_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "signkey",
        "signtoken",
        "encrytoken",
        "refreshtoken",
        "accesstoken",
        "token",
        "authorization",
        "cookie",
    }
)

# Encrypted/encoded payloads: redacted while still an opaque string blob,
# but walked into once decrypted so debug logs keep the actual data.
_ENCODED_BLOB_KEYS: frozenset[str] = frozenset(
    {
        "encrydata",
        "responddata",
        "request",
        "response",
    }
)


def redact_for_log(value: Any, *, max_string: int = 512, _depth: int = 0) -> Any:
    """Return a redacted copy of *value* suitable for debug logs."""
    if _depth > 20:
        return "<max-depth>"

    if value is None:
        return None

    if isinstance(value, str):
        if len(value) > max_string:
            return f"{value[:max_string]}…<truncated>"
        return value

    if isinstance(value, (int, float, bool)):
        return value

    if isinstance(value, bytes):
        return f"<bytes:{len(value)}b>"

    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if key.lower() in _SENSITIVE_VALUE_KEYS or (key.lower() in _ENCODED_BLOB_KEYS and isinstance(v, str)):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = redact_for_log(v, max_string=max_string, _depth=_depth + 1)
        return redacted

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_for_log(v, max_string=max_string, _depth=_depth + 1) for v in value]

    # Fallback: represent unknown objects without dumping internals.
    return repr(value)
