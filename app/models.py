"""API contract. Source of truth for the JSON shape the Flutter app reads.

Never rename, remove, or retype a field here. Adding optional fields is fine.
"""
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class DeclaredNeed(str, Enum):
    medical_equipment = "medical_equipment"
    accessibility = "accessibility"
    critical_facility = "critical_facility"


class MessageType(str, Enum):
    pre_storm = "pre_storm"
    outage_confirmed = "outage_confirmed"
    estimate_updated = "estimate_updated"
    restoration_complete = "restoration_complete"


Channel = Literal["sms", "push", "email", "phone_call"]
Band = Literal["low", "medium", "high"]


# ---------- requests ----------

class Scenario(BaseModel):
    storm_severity: int = Field(3, ge=1, le=5, description="1 = minor, 5 = severe")
    damaged_poles: int = Field(0, ge=0, le=200, description="Known or assumed damaged poles")
    crews: int = Field(2, ge=1, le=50, description="Crews assigned")
    access_ok: bool = Field(True, description="false = roads blocked")
    declared_needs: List[DeclaredNeed] = Field(
        default_factory=list, max_length=3,
        description="Opt-in only. Never inferred.",
    )


class AssessRequest(BaseModel):
    location_id: str = Field(..., max_length=32)
    scenario: Scenario = Field(default_factory=Scenario)


class MessageRequest(BaseModel):
    location_id: str = Field(..., max_length=32)
    message_type: MessageType
    scenario: Scenario = Field(default_factory=Scenario)


# ---------- response pieces ----------

class ConfirmedFact(BaseModel):
    fact: str
    text: str
    source: str
    value: Optional[float] = None


class Reason(BaseModel):
    factor: str
    text: str
    direction: Literal["increases_risk", "decreases_risk"]


class RestorationRange(BaseModel):
    low_hours: float
    high_hours: float
    median_hours: float
    method: str


class FactorDelta(BaseModel):
    change: str
    new_range: RestorationRange
    delta_text: str


class ModelEstimate(BaseModel):
    risk_band: Band
    risk_score: float = Field(..., ge=0, le=1)
    confidence: Band
    confidence_reason: str
    reasons: List[Reason]
    restoration_range: RestorationRange
    factor_deltas: List[FactorDelta]
    alert_eligible: bool
    alert_rule: str


class Escalation(BaseModel):
    trigger: str
    action: str


class CustomerActions(BaseModel):
    actions: List[str]
    safety_messages: List[str]
    channel: Channel
    send_timing: str
    escalation: Escalation


class Meta(BaseModel):
    fallback: bool = False
    fallback_reason: Optional[str] = None
    assumptions: List[str]
    limits: List[str]
    data_mode: Literal["mock", "live"]
    request_id: str


class AreaContext(BaseModel):
    """Optional, coarse, privacy-safe context about the selected area."""
    label: str
    customers_served_band: str
    upstream_device_type: Optional[str] = None
    customers_on_upstream_device_band: Optional[str] = None
    critical_facility_nearby: Optional[bool] = None


# ---------- responses ----------

class AssessResponse(BaseModel):
    location_id: str
    confirmed: List[ConfirmedFact]
    model_estimate: ModelEstimate
    customer_actions: CustomerActions
    meta: Meta
    area: Optional[AreaContext] = None


class MessageResponse(BaseModel):
    location_id: str
    message_type: MessageType
    text: str
    channel: Channel
    send_timing: str
    validated: bool
    meta: Meta


class Location(BaseModel):
    location_id: str
    label: str
    customers_served_band: str
    has_storm_history: bool


class LocationsResponse(BaseModel):
    locations: List[Location]
    data_mode: Literal["mock", "live"]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    data_mode: Literal["mock", "live"]
    files_loaded: int
    files_missing: List[str]
    backtest_auc: Optional[float] = None
