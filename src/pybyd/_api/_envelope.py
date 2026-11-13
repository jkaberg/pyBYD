"""Shared envelope building for token-authenticated requests."""

from __future__ import annotations

import json
import time
from typing import Any

from pybyd._crypto.aes import aes_encrypt_hex
from pybyd._crypto.hashing import compute_checkcode, sha1_mixed
from pybyd._crypto.signing import build_sign_string
from pybyd.config import BydConfig
from pybyd.session import Session


def build_token_outer_envelope(
    config: BydConfig,
    session: Session,
    inner: dict[str, str],
    now_ms: int,
    *,
    user_type: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Build a signed outer envelope for post-login requests.

    Parameters
    ----------
    config : BydConfig
        Client configuration.
    session : Session
        Authenticated session with tokens.
    inner : dict
        Inner payload fields to encrypt.
    now_ms : int
        Current time in milliseconds since epoch.

    user_type : str, optional
        The app's outer ``userType``. When given it is sent **and signed**:
        the cloud answers ``1007`` to a ``userType`` that is in the envelope
        but not in the signature.

    Returns
    -------
    tuple[dict, str]
        (outer_payload, content_key) tuple. The content_key is needed
        to decrypt the response's ``respondData``.
    """
    req_timestamp = str(now_ms)

    content_key = session.content_key()
    sign_key = session.sign_key()

    encry_data = aes_encrypt_hex(
        json.dumps(inner, separators=(",", ":")),
        content_key,
    )

    sign_fields: dict[str, str] = {
        **inner,
        "countryCode": config.country_code,
        "identifier": session.user_id,
        "imeiMD5": config.device.imei_md5,
        "language": config.language,
        "reqTimestamp": req_timestamp,
    }
    if user_type is not None:
        sign_fields["userType"] = user_type
    sign = sha1_mixed(build_sign_string(sign_fields, sign_key))

    outer: dict[str, Any] = {
        "countryCode": config.country_code,
        "encryData": encry_data,
        "identifier": session.user_id,
        "imeiMD5": config.device.imei_md5,
        "language": config.language,
        "reqTimestamp": req_timestamp,
        "sign": sign,
        "ostype": config.device.ostype,
        "imei": config.device.imei,
        "mac": config.device.mac,
        "model": config.device.model,
        "sdk": config.device.sdk,
        "mod": config.device.mod,
        "serviceTime": str(int(time.time() * 1000)),
    }
    if user_type is not None:
        outer["userType"] = user_type
    outer["checkcode"] = compute_checkcode(outer)

    return outer, content_key
