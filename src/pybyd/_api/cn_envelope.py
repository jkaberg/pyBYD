"""China (CN) token-authenticated outer envelope and inner base payloads."""

from __future__ import annotations

import json
import secrets
import time
from typing import Any

from pybyd._crypto.aes import aes_encrypt_hex
from pybyd._crypto.hashing import compute_cn_checkcode_payload, sha1_mixed
from pybyd._crypto.signing import build_cn_sign_string
from pybyd.config import BydConfig
from pybyd.session import Session


def build_cn_inner_base(
    config: BydConfig,
    *,
    now_ms: int | None = None,
    vin: str | None = None,
    request_serial: str | None = None,
) -> dict[str, str]:
    """Common inner fields for CN post-login requests (``buildInner`` in BYD-re ``client.js``)."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    inner: dict[str, str] = {
        "deviceName": f"{config.device.mobile_brand}{config.device.mobile_model}",
        "deviceType": config.device.device_type,
        "imeiMD5": config.device.imei_md5,
        "mobileBrand": config.device.mobile_brand,
        "mobileModel": config.device.mobile_model,
        "networkOperator": config.network_operator,
        "networkType": config.device.network_type,
        "osType": "Android",
        "osVersion": config.device.os_version,
        "random": secrets.token_hex(16).upper(),
        "softType": config.soft_type,
        "timeStamp": str(now_ms),
        "version": config.cn_app_inner_version,
    }
    if vin:
        inner["vin"] = str(vin)
    if request_serial:
        inner["requestSerial"] = str(request_serial)
    return inner


def build_cn_vehicle_list_inner(
    config: BydConfig,
    *,
    now_ms: int | None = None,
) -> dict[str, str]:
    """Vehicle list inner payload (includes ``appUiName``)."""
    inner = build_cn_inner_base(config, now_ms=now_ms)
    inner["appUiName"] = ""
    return inner


def build_cn_token_outer_envelope(
    config: BydConfig,
    session: Session,
    inner: dict[str, str],
    now_ms: int,
    *,
    user_type: str | None = None,
) -> tuple[dict[str, Any], str]:
    """CN signed outer envelope for post-login requests (WBSK layer applied by transport)."""
    _ = user_type  # reserved for parity with overseas envelope; CN uses identifierType instead
    req_timestamp = str(now_ms)
    content_key = session.content_key()
    sign_key = session.sign_key()
    api_id = session.effective_api_identifier

    encry_data = aes_encrypt_hex(
        json.dumps(inner, separators=(",", ":")),
        content_key,
    )

    id_type = 0 if inner.get("vin") else 2
    sign_fields: dict[str, Any] = {
        **inner,
        "appChannel": config.app_channel,
        "identifier": api_id,
        "identifierType": id_type,
        "imeiMD5": config.device.imei_md5,
        "reqTimestamp": req_timestamp,
        "targetBrand": config.target_brand,
        "vehicleBrand": config.vehicle_brand,
    }
    if inner.get("vin"):
        sign_fields["objective"] = inner["vin"]

    sign = sha1_mixed(build_cn_sign_string(sign_fields, sign_key))

    outer: dict[str, Any] = {
        "appChannel": config.app_channel,
        "encryData": encry_data,
        "identifier": api_id,
        "identifierType": id_type,
        "imeiMD5": config.device.imei_md5,
        "objective": inner.get("vin"),
        "outModelTypes": None,
        "reqTimestamp": req_timestamp,
        "sign": sign,
        "softType": None,
        "targetBrand": config.target_brand,
        "vehicleBrand": config.vehicle_brand,
        "version": None,
    }

    outer["ostype"] = config.device.ostype
    outer["imei"] = config.device.imei
    outer["mac"] = config.device.mac
    outer["model"] = config.device.model
    outer["sdk"] = config.device.sdk
    outer["mod"] = config.device.mod
    outer["serviceTime"] = str(int(time.time() * 1000))

    outer["checkcode"] = compute_cn_checkcode_payload({k: v for k, v in outer.items() if k != "checkcode"})

    return outer, content_key
