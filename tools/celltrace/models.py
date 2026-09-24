# Copyright (c) 2026 Luke Repko
# SPDX-License-Identifier: GPL-3.0-or-later

from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

class ReferencePoint(BaseModel):
    ah: float = Field(ge=0, allow_inf_nan=False)
    voltage_v: float = Field(ge=0, allow_inf_nan=False)

class Profile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(default='', max_length=64)
    name: str = Field(min_length=1, max_length=120)
    battery_id: str = Field(default='', max_length=120)
    manufacturer: str = Field(default='', max_length=120)
    chemistry: str = Field(default='', max_length=80)
    cells: int | None = Field(default=None, ge=1, le=100)
    nominal_v: float | None = Field(default=None, gt=0, le=1000, allow_inf_nan=False)
    expected_ah: float | None = Field(default=None, gt=0, le=100000, allow_inf_nan=False)
    expected_wh: float | None = Field(default=None, gt=0, le=1000000, allow_inf_nan=False)
    endpoint_v: float = Field(gt=0, lt=15, allow_inf_nan=False)
    notes: str = Field(default='', max_length=10000)
    conditions: str = Field(default='', max_length=2000)
    reference_label: str = Field(default='', max_length=200)
    reference_kind: Literal['manufacturer reference', 'previous measurement', 'user-defined model'] = 'user-defined model'
    reference: list[ReferencePoint] = Field(default_factory=list, max_length=30000)

    @model_validator(mode='after')
    def ordered(self):
        if self.reference and len(self.reference) < 2:
            raise ValueError('A reference curve needs at least two points')
        if any(b.ah <= a.ah for a,b in zip(self.reference, self.reference[1:])):
            raise ValueError('Reference Ah values must be strictly increasing')
        return self
