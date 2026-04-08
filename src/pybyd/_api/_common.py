"""Shared helpers for BYD API endpoint modules.

This module centralizes the most repeated patterns:
- building the common token-auth inner fields
- posting a token-enveloped request
- mapping common API error codes
- decrypting + JSON-decoding respondData

It is internal to pyBYD and may change at any time.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any

from pybyd._api._envelope import build_token_outer_envelope
from pybyd._api.cn_envelope import build_cn_inner_base, build_cn_token_outer_envelope
from pybyd._constants import SESSION_EXPIRED_CODES
from pybyd._crypto.aes import aes_decrypt_utf8
from pybyd._transport import Transport
from pybyd.config import BydConfig
from pybyd.exceptions import (
    BydApiError,
    BydEndpointNotSupportedError,
    BydSessionExpiredError,
)
from pybyd.session import Session

_logger = logging.getLogger(__name__)

#: API error codes indicating the endpoint is not supported for this vehicle.
ENDPOINT_NOT_SUPPORTED_CODES: frozenset[str] = frozenset({"1001"})


def build_inner_base(
    config: BydConfig,
    *,
    now_ms: int | None = None,
    vin: str | None = None,
    request_serial: str | None = None,
) -> dict[str, str]:
    """Build common inner payload fields used by most BYD endpoints."""
    if config.is_china_region:
        return build_cn_inner_base(config, now_ms=now_ms, vin=vin, request_serial=request_serial)

    if now_ms is None:
        now_ms = int(time.time() * 1000)

    inner: dict[str, str] = {
        "deviceType": config.device.device_type,
        "imeiMD5": config.device.imei_md5,
        "networkType": config.device.network_type,
        "random": secrets.token_hex(16).upper(),
        "timeStamp": str(now_ms),
        "version": config.app_inner_version,
    }
    if vin:
        inner["vin"] = str(vin)
    if request_serial:
        inner["requestSerial"] = str(request_serial)
    return inner


def _raise_for_code(
    *,
    endpoint: str,
    code: str,
    message: str,
    vin: str | None = None,
    not_supported_codes: frozenset[str] | None = None,
    extra_code_map: dict[frozenset[str], type[BydApiError]] | None = None,
) -> None:
    """Raise the appropriate exception for a non-zero API response code.

    Parameters
    ----------
    extra_code_map
        Optional mapping of ``frozenset[str]`` code sets to exception
        types.  Checked *before* the generic ``BydApiError`` fallback,
        but *after* session-expired and not-supported checks.
    """
    if code in SESSION_EXPIRED_CODES:
        raise BydSessionExpiredError(
            f"{endpoint} failed: code={code} message={message}",
            code=code,
            endpoint=endpoint,
        )
    if not_supported_codes is not None and code in not_supported_codes:
        suffix = f" for VIN {vin}" if vin else ""
        raise BydEndpointNotSupportedError(
            f"{endpoint} not supported{suffix} (code={code})",
            code=code,
            endpoint=endpoint,
        )
    if extra_code_map is not None:
        for codes, exc_type in extra_code_map.items():
            if code in codes:
                raise exc_type(
                    f"{endpoint} failed: code={code} message={message}",
                    code=code,
                    endpoint=endpoint,
                )
    raise BydApiError(
        f"{endpoint} failed: code={code} message={message}",
        code=code,
        endpoint=endpoint,
    )


def decode_respond_data(
    *,
    endpoint: str,
    response: dict[str, Any],
    content_key: str,
) -> Any:
    """Decrypt and JSON-decode the inner respondData payload."""
    respond_data = response.get("respondData")
    if not isinstance(respond_data, str) or not respond_data:
        return {}
    plaintext = aes_decrypt_utf8(respond_data, content_key)
    if not plaintext or not plaintext.strip():
        return {}
    try:
        decoded = json.loads(plaintext)
    except json.JSONDecodeError as exc:
        raise BydApiError(
            f"{endpoint} respondData is not JSON: {plaintext[:128]}",
            code="invalid_json",
            endpoint=endpoint,
        ) from exc

    _logger.debug("HTTP decoded endpoint=%s plaintext=%s", endpoint, plaintext)
    return decoded


async def post_token_json(
    *,
    endpoint: str,
    config: BydConfig,
    session: Session,
    transport: Transport,
    inner: dict[str, str],
    now_ms: int | None = None,
    vin: str | None = None,
    not_supported_codes: frozenset[str] | None = None,
    extra_code_map: dict[frozenset[str], type[BydApiError]] | None = None,
    user_type: str | None = None,
) -> Any:
    """Post a token-enveloped request and return decrypted JSON.

    This is a thin helper for endpoint modules; it intentionally returns `Any`
    since BYD endpoints may return objects or lists.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    if config.is_china_region:
        outer, content_key = build_cn_token_outer_envelope(
            config, session, inner, now_ms, user_type=user_type
        )
    else:
        outer, content_key = build_token_outer_envelope(
            config, session, inner, now_ms, user_type=user_type
        )

    response = await transport.post_secure(endpoint, outer)
    code = str(response.get("code", ""))
    if code != "0":
        _raise_for_code(
            endpoint=endpoint,
            code=code,
            message=str(response.get("message", "")),
            vin=vin,
            not_supported_codes=not_supported_codes,
            extra_code_map=extra_code_map,
        )

    return decode_respond_data(endpoint=endpoint, response=response, content_key=content_key)
