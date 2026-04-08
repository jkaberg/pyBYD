"""Request signing for BYD API."""

from __future__ import annotations

from typing import Any


def build_sign_string(fields: dict[str, str], password: str) -> str:
    """Build the sign string by sorting fields and appending password."""
    keys = sorted(fields.keys())
    joined = "&".join(f"{key}={'null' if fields[key] is None else fields[key]}" for key in keys)
    return f"{joined}&password={password}"


def _cn_sign_value(value: Any) -> str:
    """Stringify a sign-field value like JavaScript ``String(x)`` (``null`` → ``\"null\"``)."""
    if value is None:
        return "null"
    return str(value)


def build_cn_sign_string(fields: dict[str, Any], password: str) -> str:
    """CN signing: sorted keys, values coerced with JS-style ``String``."""
    keys = sorted(fields.keys())
    joined = "&".join(f"{key}={_cn_sign_value(fields[key])}" for key in keys)
    return f"{joined}&password={password}"
