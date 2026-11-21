"""Vehicle model."""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field, model_validator

from pybyd.models._base import BydBaseModel, BydEnum, BydTimestamp


class EnergyType(BydEnum):
    """Vehicle powertrain type.

    Maps to the BYD API's ``energyType`` (per-vehicle attribute) and
    ``powerType`` (request field on ``getEnergyConsumption``). The cloud
    transports the value as a string-encoded integer (``"0"`` / ``"1"`` /
    ``"2"``) — call sites converting to the wire format use
    ``str(int(value))``.
    """

    UNKNOWN = -1
    EV = 0
    ICE = 1
    HYBRID = 2


class EmpowerRange(BydBaseModel):
    """A permission scope granted to a shared user."""

    # BYD sometimes sends "childList" instead of "children".
    _KEY_ALIASES: ClassVar[dict[str, str]] = {
        "childList": "children",
    }

    code: str = ""
    name: str = ""
    children: list[EmpowerRange] = Field(default_factory=list)


def collect_permission_codes(ranges: list[EmpowerRange]) -> frozenset[str]:
    """Recursively collect all permission ``code`` values from an ``EmpowerRange`` tree."""
    codes: set[str] = set()
    stack = list(ranges)
    while stack:
        item = stack.pop()
        if item.code:
            codes.add(str(item.code))
        if item.children:
            stack.extend(item.children)
    return frozenset(codes)


class EmpowerDetail(BydBaseModel):
    """Detailed guest authorization record returned by ``empowerDetailByVin``."""

    vin: str = ""
    empower_id: int | None = None
    empower_status: int | None = None
    empower_control_pwd_status: bool = False
    owner_email: str = ""
    email: str = ""
    auth_period: int | None = None
    expire_date: BydTimestamp = None
    detail_list: list[EmpowerRange] = Field(default_factory=list)

    @property
    def granted_codes(self) -> frozenset[str]:
        """All permission codes granted in :attr:`detail_list`."""
        return collect_permission_codes(self.detail_list)


class Vehicle(BydBaseModel):
    """A vehicle associated with the user's account."""

    vin: str = ""
    model_name: str = ""
    brand_name: str = ""
    energy_type: EnergyType = EnergyType.UNKNOWN
    auto_alias: str = ""
    auto_plate: str = ""
    pic_main_url: str = ""
    pic_set_url: str = ""
    out_model_type: str = ""
    total_mileage: float | None = None
    model_id: int | None = None
    car_type: int | None = None
    default_car: bool = False
    empower_type: int | None = None
    permission_status: int | None = None
    tbox_version: str = ""
    not_upgrade_now_switch: str = ""
    support_ota_night_upgrade: str = ""
    vehicle_state: str = ""
    auto_bought_time: BydTimestamp = None
    yun_active_time: BydTimestamp = None
    empower_id: int | None = None
    range_detail_list: list[EmpowerRange] = Field(default_factory=list)
    # Per-feature presence/variant flags returned at the top level of
    # ``getVehicles``.  46 keys observed on a Sealion 7 EU; values look
    # tri-state: ``0`` absent / ``1`` present / ``-1`` not applicable.
    # Used by command_gating as a fine-grained second gate alongside
    # the coarser ``functionNo`` list — see Issue #47.
    vehicle_fun_learn_info: dict[str, int] = Field(default_factory=dict)

    @property
    def is_shared(self) -> bool:
        return self.empower_type is not None and self.empower_type < 0

    @property
    def granted_permission_codes(self) -> frozenset[str]:
        """All permission codes granted to a shared guest via ``rangeDetailList``."""
        return collect_permission_codes(self.range_detail_list)

    def has_permission(self, code: str) -> bool:
        """Return whether this account holds guest permission *code*.

        Owner accounts (``not is_shared``) and shared records without a populated
        ``rangeDetailList`` fail open and return ``True``.
        """
        if not self.is_shared or not self.range_detail_list:
            return True
        return code in self.granted_permission_codes

    @property
    def ota_upgrade_supported(self) -> bool:
        """Whether the vehicle's learn flags indicate mobile OTA support."""
        return self.vehicle_fun_learn_info.get("otaUpgrade") == 1

    @model_validator(mode="after")
    def _fill_missing_pics(self) -> Vehicle:
        if self.pic_main_url and self.pic_set_url:
            return self
        raw = self.raw if isinstance(self.raw, dict) else {}
        nested = raw.get("cfPic")
        if not isinstance(nested, dict):
            return self
        pic_main = self.pic_main_url or str(nested.get("picMainUrl") or nested.get("pic_main_url") or "")
        pic_set = self.pic_set_url or str(nested.get("picSetUrl") or nested.get("pic_set_url") or "")
        return self.model_copy(update={"pic_main_url": pic_main, "pic_set_url": pic_set})
