from datetime import date, datetime
from typing import Annotated, Literal
from pydantic import Field, field_validator, model_validator
from v2.domain.types import Packaging, Value, EntityId, Units, PositiveInt, PositiveAmount, Shift, Lane, require_aware
from v2.domain.settings import Settings
from .roads import Location


class Product(Packaging):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    shelf_life_minutes: PositiveInt
    active: Annotated[bool, Field(strict=True)] = True

    @field_validator('name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Product name cannot be blank')
        return value.strip()


class Mutation(Value):
    command_id: EntityId
    expected_revision: Units
    effective_date: date
    occurred_at: datetime
    reason: Annotated[str, Field(min_length=1, max_length=2000)]

    _aware = field_validator('occurred_at')(require_aware)

    @field_validator('reason')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Explain why this setup change is needed')
        return value.strip()


class ProductMutation(Mutation):
    data: Product


class Node(Location):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    kind: Literal['plant', 'hub', 'booth']
    parent_id: EntityId | None = None
    priority: Literal['Normal', 'High', 'Critical'] = 'Normal'
    active: Annotated[bool, Field(strict=True)] = True

    @model_validator(mode='after')
    def shape(self):
        if not self.name.strip():
            raise ValueError('Name cannot be blank')
        if (self.kind == 'plant') != (self.parent_id is None):
            raise ValueError('Plant has no parent; hubs and booths require a home parent')
        return self


class NodeMutation(Mutation):
    data: Node


class Driver(Value):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    licence: Annotated[str, Field(min_length=1, max_length=128)]
    user_id: EntityId
    home_id: EntityId
    active: Annotated[bool, Field(strict=True)] = True

    @field_validator('name', 'licence')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Name and licence cannot be blank')
        return value.strip()


class FleetVehicle(Value):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    home_id: EntityId
    level: Literal['plant', 'hub']
    capacity_slots: PositiveInt
    capacity_g: PositiveInt
    lane_configurations: tuple[tuple[Lane, ...], ...]
    status: Literal['active', 'maintenance', 'inactive'] = 'active'
    colour: Annotated[str, Field(pattern=r'^#[0-9a-fA-F]{6}$')]
    fuel_km_per_l: PositiveAmount
    cost_per_km: PositiveAmount
    shift: Shift
    driver_id: EntityId | None = None

    @model_validator(mode='after')
    def valid_configuration(self):
        if not self.name.strip():
            raise ValueError('Name cannot be blank')
        if not self.lane_configurations or any(not lanes or len(lanes) != len(set(lanes)) for lanes in self.lane_configurations):
            raise ValueError('Specify nonempty lane configurations without duplicate lanes')
        if len({frozenset(c) for c in self.lane_configurations}) != len(self.lane_configurations):
            raise ValueError('Duplicate lane configurations')
        return self


class DriverMutation(Mutation):
    data: Driver


class VehicleMutation(Mutation):
    data: FleetVehicle


class SettingsMutation(Mutation):
    data: Settings

    @field_validator('data')
    @classmethod
    def complete_registry(cls, value):
        if value.model_fields_set != set(Settings.model_fields):
            raise ValueError('Send the complete settings registry from GET /setup/settings to avoid resetting omitted fields')
        return value
