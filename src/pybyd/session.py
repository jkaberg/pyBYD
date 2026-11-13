"""Session state management for authenticated API calls."""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from pybyd._crypto.hashing import md5_hex


class Session(BaseModel):
    """Mutable session state after successful login.

    Parameters
    ----------
    user_id : str
        The authenticated user's ID.
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
    created_at: float = Field(default_factory=time.monotonic)
    ttl: float = 12 * 3600

    #: VIN -> whether this account reaches the car as a guest (a car shared
    #: with it through "Family and friends"). Filled from the vehicle list
    #: and corrected by the cloud's answers; see ``post_vehicle_json``.
    _guest_vins: dict[str, bool] = PrivateAttr(default_factory=dict)
    #: (VIN, endpoint) pairs whose refusal the other ``userType`` did not
    #: fix, so a genuinely unsupported endpoint is not asked twice per poll.
    _user_type_settled: set[tuple[str, str]] = PrivateAttr(default_factory=set)

    @property
    def guest_vins(self) -> dict[str, bool]:
        """Mutable VIN -> guest map shared with the endpoint helpers."""
        return self._guest_vins

    @property
    def user_type_settled(self) -> set[tuple[str, str]]:
        """Mutable set of (VIN, endpoint) pairs not worth retrying."""
        return self._user_type_settled

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
