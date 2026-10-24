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
    backup_hours: Optional[float] = Field(
        None, ge=0, le=168,
        description="Opt-in: how long the customer's backup power lasts (used with medical_equipment).",
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
    scenario_patch: Optional[dict] = Field(
        None, description="Scenario fields to apply to try this change, e.g. {'crews': 4}")


class HistogramBin(BaseModel):
    low_hours: float
    high_hours: float
    share: float = Field(..., ge=0, le=1)


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
    restoration_histogram: Optional[List[HistogramBin]] = None
    simulations: Optional[int] = None
    similar_days: Optional[int] = None   # days in the records with a storm this strong (sample size behind the risk)
    times_normal: Optional[float] = None  # risk compared with a normal day


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
    backtest: Optional["Backtest"] = None


class Backtest(BaseModel):
    """Held-out check of the risk model on the largest storm in the records."""
    event_label: str
    areas: int
    auc: float
    high_band_hit_rate: Optional[float] = None   # share of areas rated high that had damage
    low_band_hit_rate: Optional[float] = None    # share of areas rated low that had damage
    high_band_areas: int = 0
    low_band_areas: int = 0


class ChainStep(BaseModel):
    device_type: str
    customers_approx: Optional[int] = None


class AreaContext(BaseModel):
    """Optional, coarse, privacy-safe context about the selected area."""
    label: str
    customers_served_band: str
    upstream_device_type: Optional[str] = None
    customers_on_upstream_device_band: Optional[str] = None
    critical_facility_nearby: Optional[bool] = None
    upstream_chain: Optional[List[ChainStep]] = None  # from the substation down to this area


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


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(..., max_length=400)


class ChatRequest(BaseModel):
    location_id: str = Field(..., max_length=32)
    question: str = Field(..., min_length=1, max_length=300)
    scenario: Scenario = Field(default_factory=Scenario)
    history: List[ChatTurn] = Field(default_factory=list, max_length=6)  # recent turns, for follow-ups


class ChatResponse(BaseModel):
    location_id: str
    answer: str
    source: Literal["model", "template"]  # model = written by Bedrock and number-checked
    validated: bool
    suggestions: List[str]
    meta: Meta


class ZipResponse(BaseModel):
    zip: str
    location_id: str  # use this with /assess, /message, /weather
    label: str
    city: str
    customers_served_band: str


class WeatherHour(BaseModel):
    hours_ahead: int
    label: str
    precip_in: float
    gust_mph: int
    wind_from_deg: int
    temp_f: int


class WeatherAlert(BaseModel):
    event: str
    severity: str
    headline: str
    ends: Optional[str] = None
    instruction: Optional[str] = None


class WeatherModelInfo(BaseModel):
    auc_out_of_time: float
    damage_days: int
    days: int


class WeatherResponse(BaseModel):
    location_id: str
    updated_at: str
    severity: Optional[int] = Field(None, ge=1, le=5)  # null when live weather is unavailable
    severity_label: Optional[str] = None
    chance_pct: Optional[int] = None
    peak_day: Optional[Literal["today", "tomorrow"]] = None
    reasons: List[Reason] = []
    hourly: List[WeatherHour] = []
    alerts: Optional[List[WeatherAlert]] = None  # null = could not check
    fallback: bool
    fallback_reason: Optional[str] = None
    model: Optional[WeatherModelInfo] = None
    storm_type: Optional[str] = None  # hurricane, tornado, thunderstorm, winter, flood, wind, rain, none
    storm_type_label: Optional[str] = None
    arrival_hours: Optional[int] = None  # hours until storm conditions start (null = none in the next 48 hours)
    arrival_text: Optional[str] = None


class PushKeys(BaseModel):
    p256dh: str = Field(..., max_length=200)
    auth: str = Field(..., max_length=100)


class PushSubscription(BaseModel):
    endpoint: str = Field(..., max_length=600)
    keys: PushKeys
    expirationTime: Optional[float] = None


class AlertSubscribeRequest(BaseModel):
    subscription: PushSubscription
    zip: str = Field(..., pattern=r"^\d{5}$")


class AlertIdRequest(BaseModel):
    id: str = Field(..., min_length=8, max_length=64)


class AlertConfig(BaseModel):
    available: bool
    public_key: str


class AlertSubscribed(BaseModel):
    id: str
    zip: str


class AlertTestResult(BaseModel):
    sent: bool
    detail: str


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    data_mode: Literal["mock", "live"]
    files_loaded: int
    files_missing: List[str]
    backtest_auc: Optional[float] = None
    weather_model_auc: Optional[float] = None


Meta.model_rebuild()
