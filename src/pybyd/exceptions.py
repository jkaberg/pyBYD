"""Custom exception hierarchy for pybyd."""

from __future__ import annotations


class BydError(Exception):
    """Base exception for all pybyd errors."""


class BydCryptoError(BydError):
    """Encryption or decryption failure."""


class BangcleError(BydCryptoError):
    """Bangcle envelope encode/decode failure."""


class BangcleTableLoadError(BangcleError):
    """Could not load Bangcle lookup tables."""


class BydTransportError(BydError):
    """HTTP-level failure (network, non-200, invalid JSON)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        endpoint: str = "",
    ) -> None:
        self.status_code = status_code
        self.endpoint = endpoint
        super().__init__(message)


class BydApiError(BydError):
    """API returned a non-zero code (application-level error)."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        endpoint: str = "",
    ) -> None:
        self.code = code
        self.endpoint = endpoint
        super().__init__(message)


class BydAuthenticationError(BydApiError):
    """Login failed or session expired."""


class BydSessionExpiredError(BydAuthenticationError):
    """Session token rejected by the server.

    Raised when a post-login API call fails with an error code that
    indicates the token is no longer valid (e.g. ``1002``, ``1011``).
    The client catches this internally to trigger automatic
    re-authentication.
    """


class BydTokenRefreshError(BydSessionExpiredError):
    """Session token needs refresh or re-login (code ``1030``)."""


class BydServerTimeError(BydApiError):
    """Client clock differs from BYD server clock beyond threshold (code ``1003``)."""


class BydRemoteControlError(BydApiError):
    """Remote control command failed after being accepted by cloud.

    This is raised for terminal control failures (``controlState=2``)
    and for known remote-control service-level failures returned by
    control endpoints (for example code ``1009``).
    """


class BydControlPasswordError(BydApiError):
    """Remote control command rejected due invalid/locked operation password.

    Covers BYD API codes such as:
    - ``5005`` wrong operation password
    - ``5006`` cloud control temporarily locked for the day
    - ``5011`` operation password not set on the BYD account/app
    """


class BydEndpointNotSupportedError(BydApiError):
    """Endpoint not supported or not permitted for this vehicle/account (e.g. code 1001, 8002).

    Raised when the API returns an error code indicating the requested
    endpoint is not available for the user's vehicle model, region, or
    firmware version, or when a shared guest account lacks permission
    for the endpoint (for example GPS code ``8002`` or client-side
    ``guest_permission_denied``).  Consumers should stop retrying the
    endpoint for the affected VIN.
    """


class BydDataUnavailableError(BydApiError):
    """Vehicle cannot provide the requested data right now.

    Raised when the API returns an error code indicating a temporary
    data-availability issue — for example GPS code ``6051`` when the
    vehicle has no satellite fix (e.g. parked in a garage).  Consumers
    should retain the last-known value and retry later.
    """


class BydRateLimitError(BydApiError):
    """Rate limited — a previous command is still in progress (code 6024).

    This is raised when the server returns code 6024 after exhausting
    all automatic retry attempts.
    """


class BydStandbyScheduleError(BydApiError):
    """A plan's run time was refused (code 6111).

    A one-off run has to be at least 20 min ahead and within the next
    30 days.  Daily and weekly runs inside 20 min are saved instead and
    skip today, see :attr:`pybyd.models.StandbyPlanList.message`.
    """


class BydServiceBusyError(BydApiError):
    """Backend busy / soft rate-limit (code 1008, "Error de servicio").

    The dominant transient failure on EU cars: the cloud returns ``1008``
    for realtime/GPS/charging reads when it is overloaded or the car is
    deep-asleep and the backend gives up. It is recoverable — consumers
    should retain last-known data and BACK OFF (lengthen the poll
    interval) rather than hammer, since bursts of ``1008`` mean the
    backend wants fewer requests. Subclasses :class:`BydApiError` so
    existing recoverable-error handling keeps working unchanged.
    """
