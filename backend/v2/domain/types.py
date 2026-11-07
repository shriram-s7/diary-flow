"""Validated immutable value objects; transport coercion never changes unit counts."""
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Units = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
Amount = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
PositiveAmount = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
Ratio = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]
EntityId = Annotated[str, Field(strict=True, min_length=1, max_length=128, pattern=r"^\S+$")]


class Value(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)


class Lane(str, Enum):
    CHILLED = "chilled"
    AMBIENT = "ambient"
    FROZEN = "frozen"


class Role(str, Enum):
    ADMIN = "admin"
    PLANT_MANAGER = "plant_manager"
    HUB_MANAGER = "hub_manager"
    BOOTH = "booth"
    HUB_ORDERING = "hub_ordering"
    DRIVER = "driver"


class Scope(Value):
    """Shape validation only; P03 must authenticate and authorize every request."""
    role: Role
    hub_id: EntityId | None = None
    booth_id: EntityId | None = None
    driver_id: EntityId | None = None

    @model_validator(mode="after")
    def valid_scope(self):
        actual = {k for k in ("hub_id", "booth_id", "driver_id") if getattr(self, k)}
        required = {
            Role.ADMIN: set(), Role.PLANT_MANAGER: set(),
            Role.HUB_MANAGER: {"hub_id"}, Role.HUB_ORDERING: {"hub_id"},
            Role.BOOTH: {"hub_id", "booth_id"}, Role.DRIVER: {"driver_id"},
        }[self.role]
        if actual != required:
            raise ValueError("scope fields do not match role")
        return self


class Packaging(Value):
    sku_id: EntityId
    dimension: Annotated[str, Field(pattern=r"^(ml|g)$")]
    pack_size: PositiveInt
    units_per_container: PositiveInt
    full_container_g: PositiveInt
    container_type: EntityId
    returnable: Annotated[bool, Field(strict=True)]
    lane: Lane
    unit_gross_g: PositiveInt | None = None
    tare_g: Units | None = None

    @model_validator(mode="after")
    def weight_pair(self):
        if (self.unit_gross_g is None) != (self.tare_g is None):
            raise ValueError("measured weight requires both unit_gross_g and tare_g")
        if self.unit_gross_g is not None:
            if self.unit_gross_g * self.units_per_container + self.tare_g > self.full_container_g:
                raise ValueError("full_container_g must bound measured full gross weight")
        return self


class PackedQuantity(Value):
    units: Units
    slots: Units
    planning_g: Units
    returnable_containers: Units
    litres: Amount | None = None
    product_kg: Amount | None = None
    measured_gross_g: Units | None = None


def require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must have an explicit timezone")
    return value


class Shift(Value):
    start: datetime
    end: datetime

    _aware = field_validator("start", "end")(require_aware)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("shift end must be after start, including the date")
        return self


class CommandContext(Value):
    command_id: EntityId
    run_id: EntityId
    actor_id: EntityId
    expected_revision: Units
    delivery_date: date
    occurred_at: datetime
    reason: Annotated[str, Field(min_length=1, max_length=2000)] | None = None

    _aware = field_validator("occurred_at")(require_aware)


class ErrorDetail(Value):
    code: EntityId
    message: str
    field: str | None = None
    current_revision: Units | None = None


T = TypeVar("T")


class CommandResult(Value, Generic[T]):
    """Server-created result; auth/idempotency persistence are later service work."""
    schema_version: PositiveInt = 1
    correlation_id: EntityId
    revision: Units
    sim_time: datetime
    wall_time: datetime
    data: T

    _aware = field_validator("sim_time", "wall_time")(require_aware)
