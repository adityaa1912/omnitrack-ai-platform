from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from .rules import CONDITION_LOGIC, OPERATORS, RULE_TYPES


class Condition(BaseModel):
    type: str
    operator: str = "gte"
    threshold: Optional[float] = None
    class_name: Optional[str] = None
    zone_name: Optional[str] = None
    line_name: Optional[str] = None
    event_type: Optional[str] = None
    window_seconds: Optional[int] = None

    @field_validator("type")
    @classmethod
    def _validate_type(cls, value: str) -> str:
        if value not in RULE_TYPES:
            raise ValueError(f"unsupported condition type: {value}")
        return value

    @field_validator("operator")
    @classmethod
    def _validate_operator(cls, value: str) -> str:
        if value not in OPERATORS:
            raise ValueError(f"unsupported operator: {value}")
        return value


class RuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    stream_id: Optional[str] = None
    enabled: bool = True
    rule_type: Optional[str] = None
    conditions: List[Condition] = Field(default_factory=list)
    condition_logic: str = "AND"
    severity: str = "warning"
    priority: int = 0
    cooldown_seconds: int = Field(default=60, ge=0)
    dedup_key_template: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None

    @field_validator("condition_logic")
    @classmethod
    def _validate_logic(cls, value: str) -> str:
        upper = (value or "AND").upper()
        if upper not in CONDITION_LOGIC:
            raise ValueError("condition_logic must be AND or OR")
        return upper


class RuleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    stream_id: Optional[str] = None
    enabled: Optional[bool] = None
    rule_type: Optional[str] = None
    conditions: Optional[List[Condition]] = None
    condition_logic: Optional[str] = None
    severity: Optional[str] = None
    priority: Optional[int] = None
    cooldown_seconds: Optional[int] = Field(default=None, ge=0)
    dedup_key_template: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None

    @field_validator("condition_logic")
    @classmethod
    def _validate_logic(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        upper = value.upper()
        if upper not in CONDITION_LOGIC:
            raise ValueError("condition_logic must be AND or OR")
        return upper


class AcknowledgeRequest(BaseModel):
    note: Optional[str] = None


class ResolveRequest(BaseModel):
    note: Optional[str] = None


class SuppressRequest(BaseModel):
    seconds: int = Field(ge=1)
    note: Optional[str] = None
