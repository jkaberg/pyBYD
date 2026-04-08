"""China (CN) login endpoint.

Endpoint:
  - /app/auth/login
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any

from pybyd._crypto.aes import aes_decrypt_utf8, aes_encrypt_hex
from pybyd._crypto.hashing import compute_cn_checkcode_payload, md5_hex, pwd_login_key, sha1_mixed
from pybyd._crypto.signing import build_cn_sign_string
from pybyd.config import BydConfig
from pybyd.exceptions import BydAuthenticationError
from pybyd.models.token import AuthToken

_logger = logging.getLogger(__name__)

_CN_LOGIN_ENDPOINT = "/app/auth/login"


def build_cn_login_request(config: BydConfig, now_ms: int) -> dict[str, Any]:
    """Build the outer payload for CN login (before device fields + checkcode + WBSK)."""
    random_hex = secrets.token_hex(16).upper()
    req_timestamp = str(now_ms)

    inner: dict[str, str] = {
        "appInnerVersion": config.cn_app_inner_version,
        "appVersion": config.cn_app_version,
        "bluetoothMac": "",
        "city": "",
        "configVersion": "10000",
        "deviceType": config.device.device_type,
        "devicename": f"{config.device.mobile_brand}{config.device.mobile_model}",
        "imeiMD5": config.device.imei_md5,
        "isAuto": "0",
        "latitude": "",
        "longitude": "",
        "mobileBrand": config.device.mobile_brand,
        "mobileModel": config.device.mobile_model,
        "networkOperator": config.network_operator,
        "networkType": config.device.network_type,
        "osType": "Android",
        "osVersion": config.device.os_type,
        "random": random_hex,
        "softType": config.soft_type,
        "timeStamp": req_timestamp,
    }

    encry_data = aes_encrypt_hex(
        json.dumps(inner, separators=(",", ":")),
        pwd_login_key(config.password),
    )

    sign_fields: dict[str, Any] = {
        **inner,
        "appChannel": config.app_channel,
        "identifier": config.username,
        "loginType": 0,
        "reqTimestamp": req_timestamp,
        "targetBrand": config.target_brand,
    }
    sign = sha1_mixed(build_cn_sign_string(sign_fields, md5_hex(config.password)))

    outer: dict[str, Any] = {
        "appChannel": config.app_channel,
        "encryData": encry_data,
        "identifier": config.username,
        "imeiMD5": config.device.imei_md5,
        "isAuto": "0",
        "loginType": 0,
        "reqTimestamp": req_timestamp,
        "sign": sign,
        "targetBrand": config.target_brand,
    }

    outer["ostype"] = config.device.ostype
    outer["imei"] = config.device.imei
    outer["mac"] = config.device.mac
    outer["model"] = config.device.model
    outer["sdk"] = config.device.sdk
    outer["mod"] = config.device.mod
    outer["serviceTime"] = str(int(time.time() * 1000))
    outer["checkcode"] = compute_cn_checkcode_payload(
        {k: v for k, v in outer.items() if k != "checkcode"}
    )

    return outer


def parse_cn_login_response(
    outer_response: dict[str, Any],
    password: str,
    *,
    target_brand: str,
) -> AuthToken:
    """Parse CN login response."""
    if str(outer_response.get("code")) != "0":
        raise BydAuthenticationError(
            f"Login failed: code={outer_response.get('code')} message={outer_response.get('message', '')}",
            code=str(outer_response.get("code", "")),
            endpoint=_CN_LOGIN_ENDPOINT,
        )

    respond_data = outer_response.get("respondData")
    if not respond_data:
        raise BydAuthenticationError(
            "Login response missing respondData",
            endpoint=_CN_LOGIN_ENDPOINT,
        )

    plaintext = aes_decrypt_utf8(respond_data, pwd_login_key(password))
    inner = json.loads(plaintext)
    _logger.debug("HTTP decoded endpoint=%s plaintext=%s", _CN_LOGIN_ENDPOINT, plaintext)
    token = inner.get("token") if isinstance(inner, dict) else None

    if not isinstance(token, dict):
        raise BydAuthenticationError(
            "Login response missing token",
            endpoint=_CN_LOGIN_ENDPOINT,
        )

    sign_token = str(token.get("signToken") or "")
    encry_token = str(token.get("encryToken") or token.get("encryptToken") or "")

    super_id = str(token.get("superId") or "")
    brand_user_id = ""
    rel = token.get("superBindRelationDtoMap")
    if isinstance(rel, dict) and target_brand in rel:
        entry = rel[target_brand]
        if isinstance(entry, dict) and entry.get("userId") is not None:
            brand_user_id = str(entry["userId"])

    user_id = brand_user_id if brand_user_id else super_id

    if not user_id or not sign_token or not encry_token:
        raise BydAuthenticationError(
            "Login response missing token fields",
            endpoint=_CN_LOGIN_ENDPOINT,
        )

    return AuthToken(
        user_id=user_id,
        sign_token=sign_token,
        encry_token=encry_token,
        super_id=super_id if super_id else None,
        raw=token,
    )
