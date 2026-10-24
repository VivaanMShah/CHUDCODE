"""The web API: routes, the safety layer (response scrubbing and audit log), and the website.
"""
from __future__ import annotations

from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path as FsPath
import json
import logging
import os
import re
import sys
import time
import uuid

from . import assistant, data, engines
from .data import resolve
from fastapi import FastAPI, HTTPException, Path, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .models import (AssessRequest, AssessResponse, ChatRequest, ChatResponse, HealthResponse, LocationsResponse,
                     MessageRequest, MessageResponse, WeatherResponse, ZipResponse)

# ============================================================================
# SAFETY LAYER
# Safety layer: strip sensitive fields from every response, audit log, request IDs.
# ============================================================================


# Keys that must never leave the API, at any depth.
BLOCKED_KEYS = {
    "lat", "lon", "lng", "latitude", "longitude", "coords", "coordinates", "geometry", "x", "y",
    "premise", "premise_nu", "premise_id", "address", "street", "phone", "email_address", "name",
    "customer_name", "account", "account_number", "meter", "employee", "crew_id", "crew_name",
    "key", "circuit", "circuit_id", "substation", "station", "device_id", "location_n",
    "critical_c", "life_support", "facility_name", "facility_location",
}
# Decimal-degree pairs or long decimals that look like coordinates in free text.
COORD_RE = re.compile(r"-?\d{1,3}\.\d{4,}")
INTERNAL_CODE_RE = re.compile(r"\b(?:STATION\d+|CIR\d+|\d{11,})\b")


def _clean_text(s: str) -> str:
    s = COORD_RE.sub("[removed]", s)
    return INTERNAL_CODE_RE.sub("[removed]", s)


def scrub(obj):
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if k.lower() not in BLOCKED_KEYS}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    if isinstance(obj, str):
        return _clean_text(obj)
    return obj


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


_audit = None


def _audit_logger():
    global _audit
    if _audit is None:
        _audit = logging.getLogger("audit")
        _audit.setLevel(logging.INFO)
        _audit.propagate = False
        if os.getenv("AUDIT_TO_STDOUT"):  # in AWS, stdout goes to CloudWatch Logs
            _audit.addHandler(logging.StreamHandler(sys.stdout))
            return _audit
        try:
            data.LOG_DIR.mkdir(parents=True, exist_ok=True)
            _audit.addHandler(logging.FileHandler(data.LOG_DIR / "audit.log", encoding="utf-8"))
        except OSError:
            _audit.addHandler(logging.StreamHandler())
    return _audit


def mask_location(location_id: str | None) -> str | None:
    """Log only the first 3 digits of a ZIP (ZIP-240xx) so the audit log can't point at a small area."""
    if location_id and location_id.startswith("ZIP-") and len(location_id) >= 8:
        return location_id[:7] + "xx"
    return location_id


def audit(endpoint: str, request_id: str, location_id: str | None, scenario, result: dict | None,
          started: float, status: int = 200) -> None:
    """One JSON line per request. No personal data: declared needs are logged as a count only."""
    location_id = mask_location(location_id)
    entry = {
        "ts": round(time.time(), 3),
        "request_id": request_id,
        "endpoint": endpoint,
        "status": status,
        "location_id": location_id,
        "ms": round((time.time() - started) * 1000, 1),
    }
    if scenario is not None:
        entry["scenario"] = {
            "storm_severity": scenario.storm_severity, "damaged_poles": scenario.damaged_poles,
            "crews": scenario.crews, "access_ok": scenario.access_ok,
            "declared_needs_count": len(scenario.declared_needs),
        }
    if result:
        entry.update({k: result.get(k) for k in ("risk_band", "fallback", "validated", "source") if k in result})
    _audit_logger().info(json.dumps(entry))

# ============================================================================
# ROUTES
# FastAPI routes. All engine work happens in app/engine.py; data loads once at startup.
# ============================================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

STATE: engines.State | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global STATE
    STATE = engines.startup()
    yield


app = FastAPI(title="AI Outage Concierge", version="1.0.0", lifespan=lifespan)
# Local dev allows any origin. In AWS the site and API share one address, so CORS_ORIGINS is empty there.
_origins = [o for o in os.getenv("CORS_ORIGINS", "*").split(",") if o]
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET", "POST"], allow_headers=["*"])


class StripApiPrefix:
    """The website calls /api/...; the routes live at /... . Strip the prefix before routing."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            scope = dict(scope, path=scope["path"][4:], raw_path=scope.get("raw_path", b"")[4:])
        await self.inner(scope, receive, send)


app.add_middleware(StripApiPrefix)

_chat_hits: dict[str, deque] = defaultdict(deque)


def _rate_limited(request: Request, limit: int = 20, window: float = 60.0) -> bool:
    """Small per-visitor limit on /chat so one client can't run up the Bedrock bill."""
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "?"))
    now, q = time.time(), _chat_hits[ip]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def _state() -> engines.State:
    if STATE is None:
        raise HTTPException(503, "Service is starting up")
    return STATE


@app.get("/health", response_model=HealthResponse)
def health():
    s = _state()
    rep = s.store.report
    missing = sorted(k for k, r in rep.items() if r["status"] != "ok")
    return {
        "status": "ok" if s.data_mode == "live" else "degraded",
        "data_mode": s.data_mode,
        "files_loaded": sum(1 for r in rep.values() if r["status"] == "ok"),
        "files_missing": missing,
        "backtest_auc": round(s.model.auc, 3) if s.model.auc is not None else None,
        "weather_model_auc": s.weather_model["metrics"]["auc"] if s.weather_model else None,
    }


@app.get("/locations", response_model=LocationsResponse)
def locations():
    s = _state()
    locs = s.store.locations
    out = [{"location_id": r.location_id, "label": r.label,
            "customers_served_band": r.customers_served_band,
            "has_storm_history": bool(r.has_storm_history)} for r in locs.itertuples()]
    return scrub({"locations": out, "data_mode": s.data_mode})


@app.post("/assess", response_model=AssessResponse)
def assess(req: AssessRequest):
    t0, rid = time.time(), new_request_id()
    out = engines.assess(_state(), req.location_id, req.scenario, rid)
    if out is None:
        audit("/assess", rid, req.location_id, req.scenario, None, t0, 404)
        raise HTTPException(404, f"Unknown location_id: {req.location_id}")
    out = scrub(out)
    audit("/assess", rid, req.location_id, req.scenario,
                 {"risk_band": out["model_estimate"]["risk_band"], "fallback": out["meta"]["fallback"]}, t0)
    return out


@app.post("/message", response_model=MessageResponse)
def message(req: MessageRequest):
    t0, rid = time.time(), new_request_id()
    out = engines.message(_state(), req.location_id, req.message_type.value, req.scenario, rid)
    if out is None:
        audit("/message", rid, req.location_id, req.scenario, None, t0, 404)
        raise HTTPException(404, f"Unknown location_id: {req.location_id}")
    band = out.pop("_risk_band")
    out = scrub(out)
    audit("/message", rid, req.location_id, req.scenario,
                 {"risk_band": band, "fallback": out["meta"]["fallback"], "validated": out["validated"]}, t0)
    return out


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(req: ChatRequest, request: Request):
    """Ask the concierge a question. The model answers from /assess facts only, numbers are checked.
    The question text is never logged."""
    t0, rid = time.time(), new_request_id()
    if _rate_limited(request):
        raise HTTPException(429, "Too many questions. Please wait a minute and try again.")
    s = _state()
    loc = resolve(s.store, req.location_id)
    wx = engines.live_weather(s.weather_model, loc.lat, loc.lon) if loc and loc.lat is not None else None
    a = engines.assess(s, req.location_id, req.scenario, rid)
    if a is None:
        audit("/chat", rid, req.location_id, req.scenario, None, t0, 404)
        raise HTTPException(404, f"Unknown location_id: {req.location_id}")
    out = assistant.chat_answer(a, req.scenario, req.question, wx, [t.model_dump() for t in req.history])
    out = scrub(out)
    audit("/chat", rid, req.location_id, req.scenario,
                 {"fallback": out["meta"]["fallback"], "validated": out["validated"], "source": out["source"]}, t0)
    return out


ZIP_PATH = Path(..., pattern=r"^\d{5}$", description="5-digit ZIP code")


@app.get("/zip/{zip_code}", response_model=ZipResponse)
def zip_lookup(zip_code: str = ZIP_PATH):
    """Turn a ZIP into an area id. Returns no coordinates and nothing about people."""
    loc = resolve(_state().store, f"ZIP-{zip_code}")
    if loc is None:
        raise HTTPException(404, "We don't have outage data for that ZIP code yet.")
    city = loc.label.split("·")[-1].strip()
    return {"zip": zip_code, "location_id": loc.location_id, "label": loc.label, "city": city,
            "customers_served_band": loc.customers_served_band}


@app.get("/weather/{zip_code}", response_model=WeatherResponse)
def live_weather(zip_code: str = ZIP_PATH):
    """Current storm strength (1-5) from live forecasts, plus the next 12 hours and NWS alerts.
    Only the ZIP's public center point is sent to the weather services."""
    s = _state()
    loc = resolve(s.store, f"ZIP-{zip_code}")
    if loc is None:
        raise HTTPException(404, "We don't have outage data for that ZIP code yet.")
    return {"location_id": loc.location_id, **engines.live_weather(s.weather_model, loc.lat, loc.lon)}


# The website (built by Docker). Must come last so API routes win.
_static = FsPath(os.getenv("STATIC_DIR", str(data.ROOT / "dist")))
if (_static / "index.html").exists():
    app.mount("/", StaticFiles(directory=_static, html=True), name="web")
