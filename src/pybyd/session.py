"""Session state management for authenticated API calls."""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict, Field

from pybyd._crypto.hashing import md5_hex


class Session(BaseModel):
    """Mutable session state after successful login.

    Parameters
    ----------
    user_id : str
        The authenticated user's ID (brand-specific on CN when available).
    super_id : str or None
        CN super account id for MQTT username / outer identifier.
    sign_token : str
        Token used for request signature derivation.
    encry_token : str
        Token used for content encryption key derivation.
    created_at : float
        Monotonic timestamp (``time.monotonic()``) when the session
        was created.  Defaults to *now* if not provided.
    ttl : float
        Time-to-live in seconds.  After this period the session is
        considered expired and should be refreshed via a new login.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_default=True,
        str_strip_whitespace=True,
    )

    user_id: str
    sign_token: str
    encry_token: str
    super_id: str | None = None
    created_at: float = Field(default_factory=time.monotonic)
    ttl: float = 12 * 3600

    @property
    def effective_api_identifier(self) -> str:
        """Identifier for CN outer payloads and MQTT (``superId`` preferred)."""
        if self.super_id:
            return self.super_id
        return self.user_id

    def content_key(self) -> str:
        """AES key for encrypting/decrypting inner payload data.

        Derived as ``MD5(encry_token)`` in uppercase hex.
        Cached because the frozen model guarantees the token never changes.
        """
        # Use a simple cache attribute to avoid recomputing on every call.
        # Cannot use @functools.cached_property on a frozen Pydantic model
        # directly, so we stash it via object.__setattr__.
        try:
            return str(object.__getattribute__(self, "_content_key_cache"))
        except AttributeError:
            value = md5_hex(self.encry_token)
            object.__setattr__(self, "_content_key_cache", value)
            return value

    def sign_key(self) -> str:
        """Key used in request signature computation.

        Derived as ``MD5(sign_token)`` in uppercase hex.
        Cached because the frozen model guarantees the token never changes.
        """
        try:
            return str(object.__getattribute__(self, "_sign_key_cache"))
        except AttributeError:
            value = md5_hex(self.sign_token)
            object.__setattr__(self, "_sign_key_cache", value)
            return value

    @property
    def is_expired(self) -> bool:
        """Whether the session has exceeded its TTL."""
        return (time.monotonic() - self.created_at) >= self.ttl

    @property
    def age(self) -> float:
        """Seconds since the session was created."""
        return time.monotonic() - self.created_at
