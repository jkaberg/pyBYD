"""Login endpoint.

Endpoint:
  - /app/account/login
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any

from pybyd._crypto.aes import aes_decrypt_utf8, aes_encrypt_hex
from pybyd._crypto.hashing import compute_checkcode, md5_hex, pwd_login_key, sha1_mixed
from pybyd._crypto.signing import build_sign_string
from pybyd.config import BydConfig
from pybyd.exceptions import BydAuthenticationError
from pybyd.models.token import AuthToken

_logger = logging.getLogger(__name__)


def _common_outer_fields(config: BydConfig) -> dict[str, str]:
    """Build the common outer fields from device profile."""
    return {
        "ostype": config.device.ostype,
        "imei": config.device.imei,
        "mac": config.device.mac,
        "model": config.device.model,
        "sdk": config.device.sdk,
        "mod": config.device.mod,
    }


def build_login_request(config: BydConfig, now_ms: int) -> dict[str, Any]:
    """Build the outer payload for the login endpoint.

    Parameters
    ----------
    config : BydConfig
        Client configuration.
    now_ms : int
        Current time in milliseconds since epoch.

    Returns
    -------
    dict
        The outer payload ready for Bangcle encoding.
    """
    random_hex = secrets.token_hex(16).upper()
    req_timestamp = str(now_ms)
    service_time = str(int(time.time() * 1000))

    inner: dict[str, str] = {
        "appInnerVersion": config.app_inner_version,
        "appVersion": config.app_version,
        "deviceName": f"{config.device.mobile_brand}{config.device.mobile_model}",
        "deviceType": config.device.device_type,
        "imeiMD5": config.device.imei_md5,
        "isAuto": config.is_auto,
        "mobileBrand": config.device.mobile_brand,
        "mobileModel": config.device.mobile_model,
        "networkType": config.device.network_type,
        "osType": config.device.os_type,
        "osVersion": config.device.os_version,
        "random": random_hex,
        "softType": config.soft_type,
        "timeStamp": req_timestamp,
        "timeZone": config.time_zone,
    }

    encry_data = aes_encrypt_hex(
        json.dumps(inner, separators=(",", ":")),
        pwd_login_key(config.password),
    )

    password_md5 = md5_hex(config.password)
    sign_fields: dict[str, str] = {
        **inner,
        "countryCode": config.country_code,
        "functionType": "pwdLogin",
        "identifier": config.username,
        "identifierType": "0",
        "language": config.language,
        "reqTimestamp": req_timestamp,
    }
    sign = sha1_mixed(build_sign_string(sign_fields, password_md5))

    outer: dict[str, Any] = {
        "countryCode": config.country_code,
        "encryData": encry_data,
        "functionType": "pwdLogin",
        "identifier": config.username,
        "identifierType": "0",
        "imeiMD5": config.device.imei_md5,
        "isAuto": config.is_auto,
        "language": config.language,
        "reqTimestamp": req_timestamp,
        "sign": sign,
        "signKey": config.password,
        **_common_outer_fields(config),
        "serviceTime": service_time,
    }
    outer["checkcode"] = compute_checkcode(outer)

    return outer


def parse_login_response(
    outer_response: dict[str, Any],
    password: str,
) -> AuthToken:
    """Parse login response and extract the auth token.

    Parameters
    ----------
    outer_response : dict
        Decoded outer response from the API.
    password : str
        Plaintext password to derive the login AES key.

    Returns
    -------
    AuthToken
        The parsed authentication token.

    Raises
    ------
    BydAuthenticationError
        If login failed or response is missing token fields.
    """
    if str(outer_response.get("code")) != "0":
        raise BydAuthenticationError(
            f"Login failed: code={outer_response.get('code')} message={outer_response.get('message', '')}",
            code=str(outer_response.get("code", "")),
            endpoint="/app/account/login",
        )

    respond_data = outer_response.get("respondData")
    if not respond_data:
        raise BydAuthenticationError(
            "Login response missing respondData",
            endpoint="/app/account/login",
        )

    plaintext = aes_decrypt_utf8(respond_data, pwd_login_key(password))
    inner = json.loads(plaintext)
    _logger.debug("HTTP decoded endpoint=/app/account/login plaintext=%s", plaintext)
    token = inner.get("token") if isinstance(inner, dict) else None

    if not token or not token.get("userId") or not token.get("signToken") or not token.get("encryToken"):
        raise BydAuthenticationError(
            "Login response missing token fields",
            endpoint="/app/account/login",
        )

    return AuthToken(
        user_id=str(token["userId"]),
        sign_token=str(token["signToken"]),
        encry_token=str(token["encryToken"]),
        super_id=None,
        raw=token,
    )
