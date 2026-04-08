"""HTTP transport with Bangcle or WBSK envelope wrapping."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any, Protocol

import aiohttp

from pybyd._constants import USER_AGENT
from pybyd.config import BydConfig
from pybyd.exceptions import BydTransportError

_logger = logging.getLogger(__name__)


class EnvelopeCodec(Protocol):
    """Encode/decode outer JSON string for the wire format (Bangcle or WBSK)."""

    async def async_load_tables(self) -> None: ...

    def encode_envelope(self, plaintext: str | bytes) -> str: ...

    def decode_envelope(self, envelope: str) -> bytes: ...


class Transport(Protocol):
    """Structural transport interface used by endpoint modules.

    Having a protocol here makes it easy to pass test doubles/mocks while
    keeping the production implementation (`SecureTransport`) concrete.
    """

    async def post_secure(self, endpoint: str, outer_payload: Mapping[str, Any]) -> dict[str, Any]: ...


class SecureTransport:
    """HTTP transport that handles Bangcle or WBSK envelope encoding.

    Cookie persistence is delegated to the ``aiohttp.ClientSession``'s
    built-in ``CookieJar`` — callers should create the session with
    ``cookie_jar=aiohttp.CookieJar(unsafe=True)`` for single-host APIs.
    """

    def __init__(
        self,
        config: BydConfig,
        codec: EnvelopeCodec,
        http_session: aiohttp.ClientSession,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._codec = codec
        self._http = http_session
        self._logger = logger or _logger

    async def post_secure(self, endpoint: str, outer_payload: Mapping[str, Any]) -> dict[str, Any]:
        """Send a signed request through the envelope layer.

        1. JSON-encode the outer payload
        2. Codec-encode it (Bangcle ``F``+base64 or WBSK base64)
        3. POST as ``{"request": "<encoded>"}``
        4. Decode the ``{"response": "<encoded>"}`` reply
        5. Return the decoded JSON dict
        """
        encoded = self._codec.encode_envelope(json.dumps(outer_payload, separators=(",", ":"), ensure_ascii=False))

        headers: dict[str, str] = {
            "accept-encoding": "identity",
            "content-type": "application/json; charset=UTF-8",
            "user-agent": USER_AGENT,
        }
        if self._config.is_china_region:
            headers["version"] = self._config.cn_app_inner_version
            headers["platform"] = "ANDROID"
            headers["BrandFlag"] = self._config.brand_flag

        url = f"{self._config.base_url}{endpoint}"
        body = json.dumps({"request": encoded})

        self._logger.debug("HTTP POST %s", url)

        try:
            async with self._http.post(url, data=body, headers=headers) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise BydTransportError(
                        f"HTTP {resp.status} from {endpoint}: {text[:200]}",
                        status_code=resp.status,
                        endpoint=endpoint,
                    )
        except BydTransportError:
            raise
        except aiohttp.ClientError as exc:
            raise BydTransportError(
                f"Request to {endpoint} failed: {exc}",
                endpoint=endpoint,
            ) from exc

        try:
            body_json = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BydTransportError(
                f"Invalid JSON from {endpoint}: {text[:200]}",
                endpoint=endpoint,
            ) from exc

        if not isinstance(body_json, dict) or "response" not in body_json:
            raise BydTransportError(
                f"Missing 'response' field from {endpoint}",
                endpoint=endpoint,
            )

        response_str = body_json["response"]
        if not isinstance(response_str, str) or not response_str.strip():
            raise BydTransportError(
                f"Empty response payload from {endpoint}",
                endpoint=endpoint,
            )

        decoded_text = self._codec.decode_envelope(response_str).decode("utf-8").strip()

        # Handle stray F prefix on decoded JSON (observed in some Bangcle responses)
        if decoded_text.startswith("F{") or decoded_text.startswith("F["):
            decoded_text = decoded_text[1:]

        try:
            result: dict[str, Any] = json.loads(decoded_text)
        except json.JSONDecodeError as exc:
            raise BydTransportError(
                f"Envelope response from {endpoint} is not JSON: {decoded_text[:64]}",
                endpoint=endpoint,
            ) from exc

        return result
