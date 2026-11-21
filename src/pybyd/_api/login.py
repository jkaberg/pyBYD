"""Login endpoint.

Endpoint:
  - /app/account/login
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from typing import Any

from pybyd._constants import SERVER_TIME_DIFF_CODES
from pybyd._crypto.aes import aes_decrypt_utf8, aes_encrypt_hex
from pybyd._crypto.hashing import compute_checkcode, md5_hex, pwd_login_key, sha1_mixed
from pybyd._crypto.signing import build_sign_string
from pybyd._redact import redact_for_log
from pybyd._transport import Transport
from pybyd._version import __version__ as _pybyd_version
from pybyd.config import BydConfig
from pybyd.exceptions import BydAuthenticationError, BydServerTimeError
from pybyd.models.token import AuthToken
from pybyd.session import Session


def _clean_app_version(version: str) -> str:
    """Strip PEP 440 dev/local suffixes so BYD accepts ``appName``."""
    match = re.match(r"^\d+(?:\.\d+){0,2}", version)
    return match.group(0) if match else "0.0.0"


_APP_NAME = f"pyBYD+{_clean_app_version(_pybyd_version)}"

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
        "agreeStatus": "0",
        "agreementType": "[1,2]",
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
        "appName": _APP_NAME,
        "countryCode": config.country_code,
        "functionType": "pwdLogin",
        "identifier": config.username,
        "identifierType": config.identifier_type,
        "language": config.language,
        "reqTimestamp": req_timestamp,
    }
    sign = sha1_mixed(build_sign_string(sign_fields, password_md5))

    outer: dict[str, Any] = {
        "appName": _APP_NAME,
        "countryCode": config.country_code,
        "encryData": encry_data,
        "functionType": "pwdLogin",
        "identifier": config.username,
        "identifierType": config.identifier_type,
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
    code = str(outer_response.get("code", ""))
    if code != "0":
        if code in SERVER_TIME_DIFF_CODES:
            raise BydServerTimeError(
                f"Login failed: code={code} message={outer_response.get('message', '')}",
                code=code,
                endpoint="/app/account/login",
            )
        raise BydAuthenticationError(
            f"Login failed: code={outer_response.get('code')} message={outer_response.get('message', '')}",
            code=code,
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
    _logger.debug("HTTP decoded endpoint=/app/account/login payload=%s", redact_for_log(inner))
    token = inner.get("token") if isinstance(inner, dict) else None

    if not token or not token.get("userId") or not token.get("signToken") or not token.get("encryToken"):
        raise BydAuthenticationError(
            "Login response missing token fields",
            endpoint="/app/account/login",
        )

    new_tokens = inner.get("newTokens") if isinstance(inner, dict) else None
    refresh_token = ""
    if isinstance(new_tokens, dict) and new_tokens.get("refreshToken"):
        refresh_token = str(new_tokens["refreshToken"])
    elif isinstance(token, dict) and token.get("refreshToken"):
        refresh_token = str(token["refreshToken"])

    return AuthToken(
        user_id=str(token["userId"]),
        sign_token=str(token["signToken"]),
        encry_token=str(token["encryToken"]),
        refresh_token=refresh_token,
        raw=token,
    )


async def refresh_session_token(
    config: BydConfig,
    session: Session,
    transport: Transport,
) -> AuthToken:
    """Refresh ``encryToken`` and ``signToken`` via ``/app/token/refresh``."""
    from pybyd._api._common import build_inner_base, post_token_json

    inner = build_inner_base(config)
    inner["refreshToken"] = session.refresh_token
    decoded = await post_token_json(
        endpoint="/app/token/refresh",
        config=config,
        session=session,
        transport=transport,
        inner=inner,
    )
    payload = decoded if isinstance(decoded, dict) else {}
    token_obj = payload.get("newTokens") if isinstance(payload.get("newTokens"), dict) else payload
    if not isinstance(token_obj, dict) or not token_obj.get("signToken") or not token_obj.get("encryToken"):
        raise BydAuthenticationError(
            "Token refresh response missing signToken/encryToken",
            endpoint="/app/token/refresh",
        )
    return AuthToken(
        user_id=str(token_obj.get("userId") or session.user_id),
        sign_token=str(token_obj["signToken"]),
        encry_token=str(token_obj["encryToken"]),
        refresh_token=str(token_obj.get("refreshToken") or session.refresh_token),
        raw=token_obj,
    )
