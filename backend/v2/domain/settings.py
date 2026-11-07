"""Prototype-1 defaults. Persist and snapshot revisions in later services."""
from datetime import time
from decimal import Decimal
from typing import Literal

from pydantic import model_validator

from .types import Amount, PositiveAmount, PositiveInt, Ratio, Value


class Settings(Value):
    app_name: str = "Allocra"
    business_timezone: Literal["Asia/Kolkata"] = "Asia/Kolkata"
    suggestion_time: time = time(14)
    booth_cutoff: time = time(16)
    hub_default_time: time = time(17)
    hub_cutoff: time = time(18)
    plant_dispatch: time = time(20)
    hub_dispatch: time = time(1)
    booth_ready_by: time = time(5)
    day_close: time = time(6)
    booth_approval_limit: Ratio = Decimal("0.30")
    hub_approval_limit: Ratio = Decimal("0.20")
    minimum_share: Ratio = Decimal("0.80")
    hub_buffer_z: Amount = Decimal("1")
    van_buffer_z: Amount = Decimal("0.5")
    planning_speed_kmph: PositiveAmount = Decimal("30")
    rain_factor: Ratio = Decimal("0.6")
    traffic_factor: Ratio = Decimal("0.4")
    dock_minutes: Amount = Decimal("5")
    minutes_per_slot: Amount = Decimal("0.2")
    loading_minutes: Amount = Decimal("5")
    fuel_km_per_l: PositiveAmount = Decimal("5")
    cost_per_km: Amount = Decimal("18")
    risk_low_minutes: PositiveAmount = Decimal("30")
    risk_medium_minutes: Amount = Decimal("10")
    shelf_life_margin_minutes: Amount = Decimal("0")
    detection_delay_minutes: Amount = Decimal("5")
    detection_proximity_m: PositiveAmount = Decimal("300")
    detour_budget_seconds: PositiveAmount = Decimal("4")
    detour_attempts: PositiveInt = 2
    costly_detour_minutes: Amount = Decimal("15")
    costly_detour_ratio: Amount = Decimal("0.30")
    reminder_minutes: Amount = Decimal("30")
    acknowledgment_minutes: PositiveAmount = Decimal("5")
    speed_presets: tuple[PositiveInt, ...] = (1, 5, 15, 30, 60, 120)
    tick_budget_ms: PositiveInt = 200
    api_p95_budget_ms: PositiveInt = 100

    @model_validator(mode="after")
    def ordered_settings(self):
        previous_day = (self.suggestion_time, self.booth_cutoff, self.hub_default_time,
                        self.hub_cutoff, self.plant_dispatch)
        delivery_day = (self.hub_dispatch, self.booth_ready_by, self.day_close)
        for group in (previous_day, delivery_day):
            if any(t.tzinfo is not None for t in group):
                raise ValueError("event times are naive business-local wall times")
            if any(a >= b for a, b in zip(group, group[1:])):
                raise ValueError("daily event times must be strictly ordered")
        if not self.app_name.strip():
            raise ValueError("app_name cannot be blank")
        if self.rain_factor == 0 or self.traffic_factor == 0:
            raise ValueError("speed factors must be positive; closures are separate")
        if self.risk_medium_minutes >= self.risk_low_minutes:
            raise ValueError("risk thresholds must be ordered")
        if not self.speed_presets or tuple(sorted(set(self.speed_presets))) != self.speed_presets:
            raise ValueError("speed presets must be nonempty, unique and increasing")
        if self.speed_presets[-1] > 120:
            raise ValueError("prototype speed cannot exceed 120x")
        return self


DEFAULTS = Settings()
