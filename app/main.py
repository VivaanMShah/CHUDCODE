"""FastAPI routes. All engine work happens in app/engine.py; data loads once at startup."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import engine, safety
from .models import (AssessRequest, AssessResponse, HealthResponse, LocationsResponse,
                     MessageRequest, MessageResponse)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

STATE: engine.State | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global STATE
    STATE = engine.startup()
    yield


app = FastAPI(title="AI Outage Concierge", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])


def _state() -> engine.State:
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
    }


@app.get("/locations", response_model=LocationsResponse)
def locations():
    s = _state()
    locs = s.store.locations
    out = [{"location_id": r.location_id, "label": r.label,
            "customers_served_band": r.customers_served_band,
            "has_storm_history": bool(r.has_storm_history)} for r in locs.itertuples()]
    return safety.scrub({"locations": out, "data_mode": s.data_mode})


@app.post("/assess", response_model=AssessResponse)
def assess(req: AssessRequest):
    t0, rid = time.time(), safety.new_request_id()
    out = engine.assess(_state(), req.location_id, req.scenario, rid)
    if out is None:
        safety.audit("/assess", rid, req.location_id, req.scenario, None, t0, 404)
        raise HTTPException(404, f"Unknown location_id: {req.location_id}")
    out = safety.scrub(out)
    safety.audit("/assess", rid, req.location_id, req.scenario,
                 {"risk_band": out["model_estimate"]["risk_band"], "fallback": out["meta"]["fallback"]}, t0)
    return out


@app.post("/message", response_model=MessageResponse)
def message(req: MessageRequest):
    t0, rid = time.time(), safety.new_request_id()
    out = engine.message(_state(), req.location_id, req.message_type.value, req.scenario, rid)
    if out is None:
        safety.audit("/message", rid, req.location_id, req.scenario, None, t0, 404)
        raise HTTPException(404, f"Unknown location_id: {req.location_id}")
    band = out.pop("_risk_band")
    out = safety.scrub(out)
    safety.audit("/message", rid, req.location_id, req.scenario,
                 {"risk_band": band, "fallback": out["meta"]["fallback"], "validated": out["validated"]}, t0)
    return out
