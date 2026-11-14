"""Guest accounts declare ``userType`` on the endpoints that reach the car.

Since 2026-11-12 the BYD cloud answers ``code=1001`` on realtime, HVAC
status, GPS and remote control to an account the car was shared with
("Family and friends") unless the outer envelope carries a signed
``userType: "1"``. The fake transport below behaves like that cloud.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from pybyd._api import gps as gps_api
from pybyd._api import hvac as hvac_api
from pybyd._api._common import build_inner_base, post_token_json, post_vehicle_json
from pybyd._api._envelope import build_token_outer_envelope
from pybyd._crypto.aes import aes_encrypt_hex
from pybyd._crypto.hashing import sha1_mixed
from pybyd._crypto.signing import build_sign_string
from pybyd.client import BydClient
from pybyd.config import BydConfig
from pybyd.exceptions import BydApiError, BydDataUnavailableError, BydEndpointNotSupportedError
from pybyd.models.vehicle import Vehicle
from pybyd.session import Session

VIN = "TESTVIN0000000001"
ENDPOINT = "/control/getStatusNow"


def _config() -> BydConfig:
    return BydConfig(username="user@example.com", password="pw")


def _session() -> Session:
    return Session(user_id="42", sign_token="sign", encry_token="encry")


class FakeCloud:
    """Answers like the BYD cloud does to one kind of account.

    ``guest=True``: the car is shared with the account, so a request without
    ``userType`` is refused with 1001. ``guest=False``: the account owns the
    car, so a request with it is refused with 8003. ``unsupported`` refuses
    either way, like an endpoint the car really does not have.
    """

    def __init__(self, session: Session, *, guest: bool, unsupported: bool = False, answer: str = "0") -> None:
        self._session = session
        self.guest = guest
        self.unsupported = unsupported
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    async def post_secure(self, endpoint: str, outer: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(outer)
        declared = outer.get("userType") == "1"
        if declared and not self.guest:
            return {"code": "8003", "message": "Vehicle authorization invalid"}
        if not declared and (self.guest or self.unsupported):
            return {"code": "1001", "message": "service exception"}
        if self.unsupported:
            return {"code": "1001", "message": "service exception"}
        if self.answer != "0":
            return {"code": self.answer, "message": "refused"}
        body = json.dumps({"ok": 1})
        return {"code": "0", "respondData": aes_encrypt_hex(body, self._session.content_key())}


async def _post(session: Session, cloud: FakeCloud) -> Any:
    config = _config()
    return await post_vehicle_json(
        endpoint=ENDPOINT,
        config=config,
        session=session,
        transport=cloud,  # type: ignore[arg-type]
        inner=build_inner_base(config, vin=VIN),
        vin=VIN,
        not_supported_codes=frozenset({"1001"}),
    )


def test_user_type_is_signed() -> None:
    config = _config()
    session = _session()
    inner = build_inner_base(config, now_ms=1, vin=VIN)
    outer, _ = build_token_outer_envelope(config, session, inner, 1, user_type="1")
    fields = {
        **inner,
        "countryCode": config.country_code,
        "identifier": session.user_id,
        "imeiMD5": config.device.imei_md5,
        "language": config.language,
        "reqTimestamp": "1",
    }
    assert outer["userType"] == "1"
    assert outer["sign"] == sha1_mixed(build_sign_string({**fields, "userType": "1"}, session.sign_key()))
    assert outer["sign"] != sha1_mixed(build_sign_string(fields, session.sign_key()))


def test_envelope_without_user_type_is_unchanged() -> None:
    config = _config()
    outer, _ = build_token_outer_envelope(config, _session(), build_inner_base(config, vin=VIN), 1)
    assert "userType" not in outer


async def test_known_guest_declares_itself_at_once() -> None:
    session = _session()
    session.guest_vins[VIN] = True
    cloud = FakeCloud(session, guest=True)
    assert await _post(session, cloud) == {"ok": 1}
    assert [c.get("userType") for c in cloud.calls] == ["1"]


async def test_owner_never_sends_user_type() -> None:
    session = _session()
    cloud = FakeCloud(session, guest=False)
    assert await _post(session, cloud) == {"ok": 1}
    assert [c.get("userType") for c in cloud.calls] == [None]


async def test_unlisted_guest_is_corrected_by_the_answer() -> None:
    session = _session()
    cloud = FakeCloud(session, guest=True)
    assert await _post(session, cloud) == {"ok": 1}
    assert [c.get("userType") for c in cloud.calls] == [None, "1"]
    assert session.guest_vins[VIN] is True
    # The retry is a new request, not a replay of the first one.
    assert cloud.calls[0]["encryData"] != cloud.calls[1]["encryData"]

    cloud.calls.clear()
    await _post(session, cloud)
    assert [c.get("userType") for c in cloud.calls] == ["1"]


async def test_wrongly_flagged_owner_is_corrected_by_the_answer() -> None:
    session = _session()
    session.guest_vins[VIN] = True
    cloud = FakeCloud(session, guest=False)
    assert await _post(session, cloud) == {"ok": 1}
    assert [c.get("userType") for c in cloud.calls] == ["1", None]
    assert session.guest_vins[VIN] is False


async def test_unsupported_endpoint_still_reads_as_1001_and_is_asked_once_after() -> None:
    session = _session()
    cloud = FakeCloud(session, guest=False, unsupported=True)
    with pytest.raises(BydEndpointNotSupportedError) as exc:
        await _post(session, cloud)
    assert exc.value.code == "1001"
    assert len(cloud.calls) == 2
    assert session.guest_vins.get(VIN, False) is False

    cloud.calls.clear()
    with pytest.raises(BydEndpointNotSupportedError):
        await _post(session, cloud)
    assert len(cloud.calls) == 1


async def test_retry_that_gets_past_the_check_reports_the_real_answer() -> None:
    """A sleeping car answers the corrected request with 6002: that is the answer."""
    session = _session()
    cloud = FakeCloud(session, guest=True, answer="6002")
    with pytest.raises(BydDataUnavailableError) as exc:
        await _post(session, cloud)
    assert exc.value.code == "6002"
    assert session.guest_vins[VIN] is True
    assert (VIN, ENDPOINT) not in session.user_type_settled


async def test_other_errors_are_not_retried() -> None:
    session = _session()
    cloud = FakeCloud(session, guest=False, answer="6002")
    with pytest.raises(BydApiError):
        await _post(session, cloud)
    assert len(cloud.calls) == 1


async def test_token_endpoints_are_untouched() -> None:
    """smartCharge and the rest go through post_token_json: no userType, no retry."""
    session = _session()
    session.guest_vins[VIN] = True
    cloud = FakeCloud(session, guest=False)
    config = _config()
    await post_token_json(
        endpoint="/control/smartCharge/homePage",
        config=config,
        session=session,
        transport=cloud,  # type: ignore[arg-type]
        inner=build_inner_base(config, vin=VIN),
        vin=VIN,
    )
    assert [c.get("userType") for c in cloud.calls] == [None]


async def test_read_endpoints_go_through_the_vehicle_helper() -> None:
    session = _session()
    session.guest_vins[VIN] = True
    cloud = FakeCloud(session, guest=True)
    config = _config()
    await hvac_api.fetch_hvac_status(config, session, cloud, VIN)  # type: ignore[arg-type]
    await gps_api.fetch_gps_endpoint("/control/getGpsInfo", config, session, cloud, VIN)  # type: ignore[arg-type]
    assert [c.get("userType") for c in cloud.calls] == ["1", "1"]


@pytest.mark.parametrize(
    ("raw", "guest"),
    [
        ({"vin": VIN, "empowerType": -1, "empowerId": 123}, True),
        ({"vin": VIN, "empowerType": 3}, False),
        ({"vin": VIN}, False),
    ],
)
async def test_vehicle_list_seeds_the_guess_and_it_survives_a_login(
    raw: dict[str, Any], guest: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = BydClient(_config())

    async def _fake_authed_call(fn: Any, *args: Any, **kwargs: Any) -> list[Vehicle]:
        return [Vehicle.model_validate(raw)]

    monkeypatch.setattr(client, "_authed_call", _fake_authed_call)
    await client.get_vehicles()
    assert client._guest_vins[VIN] is guest
