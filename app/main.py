"""The web API: routes, the safety layer (response scrubbing and audit log), and the website.
"""
from __future__ import annotations

from collections import defaultdict, deque
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from pathlib import Path as FsPath
import asyncio
import base64
import hashlib
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

from .models import (AlertConfig, AlertIdRequest, AlertSubscribeRequest, AlertSubscribed, AlertTestResult,
                     AssessRequest, AssessResponse, ChatRequest, ChatResponse, HealthResponse, LocationsResponse,
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
    loop = asyncio.create_task(_alert_loop())
    yield
    loop.cancel()


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

_chat_hits: dict[tuple, deque] = defaultdict(deque)


def _rate_limited(request: Request, limit: int = 20, window: float = 60.0, bucket: str = "chat") -> bool:
    """Small per-visitor limit on /chat so one client can't run up the Bedrock bill."""
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "?"))
    now, q = time.time(), _chat_hits[(bucket, ip)]
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


# ============================================================================
# PHONE ALERTS (web push)
# Alerts that arrive on a phone like a text. Real SMS needs a carrier-registered sending number, which this
# AWS account does not have, so this uses web push: add the site to the home screen and allow notifications.
# We keep only the push address and the ZIP (no name or phone number) and delete both on turn-off.
# ============================================================================

PUSH_HOSTS = (".googleapis.com", ".mozilla.com", ".push.apple.com", ".notify.windows.com")  # real push services only
ALERT_TTL_DAYS = 30
ALERT_CHECK_SECONDS = 600


class AlertStore:
    """Subscriptions live in DynamoDB in AWS (ALERTS_TABLE), and in memory when running locally."""

    def __init__(self):
        self.mem: dict[str, dict] = {}
        self.table = None
        name = os.getenv("ALERTS_TABLE")
        if name:
            import boto3

            self.table = boto3.resource("dynamodb", region_name=os.getenv("AWS_REGION") or "us-east-1").Table(name)

    def put(self, item: dict) -> None:
        item = {**item, "ttl": int(time.time()) + ALERT_TTL_DAYS * 86400}
        if self.table:
            self.table.put_item(Item=item)
        else:
            self.mem[item["id"]] = item

    def get(self, sub_id: str) -> dict | None:
        if self.table:
            return self.table.get_item(Key={"id": sub_id}).get("Item")
        return self.mem.get(sub_id)

    def delete(self, sub_id: str) -> None:
        if self.table:
            self.table.delete_item(Key={"id": sub_id})
        else:
            self.mem.pop(sub_id, None)

    def all(self) -> list[dict]:
        if not self.table:
            return list(self.mem.values())
        items, kw = [], {}
        while True:
            r = self.table.scan(**kw)
            items += r.get("Items", [])
            if "LastEvaluatedKey" not in r:
                return items
            kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


ALERTS = AlertStore()
_vapid_key = None


def _vapid():
    """The signing key. In AWS it comes from the VAPID_PRIVATE_PEM secret; locally a temporary one is made."""
    global _vapid_key
    if _vapid_key is None:
        from py_vapid import Vapid

        pem = os.getenv("VAPID_PRIVATE_PEM")
        if pem:
            _vapid_key = Vapid.from_pem(pem.encode())
        else:
            log.warning("alerts: no VAPID_PRIVATE_PEM, using a temporary key (subscriptions will not survive a restart)")
            _vapid_key = Vapid()
            _vapid_key.generate_keys()
    return _vapid_key


def _public_key() -> str:
    from cryptography.hazmat.primitives import serialization

    raw = _vapid().public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _valid_endpoint(endpoint: str) -> bool:
    u = urlparse(endpoint)
    host = u.hostname or ""
    return u.scheme == "https" and any(host.endswith(h) or host == h.lstrip(".") for h in PUSH_HOSTS)


def push(sub: dict, payload: dict) -> str:
    """Send one notification. Returns 'ok', 'gone' (the phone unsubscribed), or 'error'."""
    from pywebpush import WebPushException, webpush

    try:
        webpush(subscription_info=sub, data=json.dumps(payload), vapid_private_key=_vapid(),
                vapid_claims={"sub": os.getenv("VAPID_CONTACT", "mailto:outage-concierge@example.com")},
                ttl=3600, timeout=10)
        return "ok"
    except WebPushException as e:
        return "gone" if getattr(e.response, "status_code", None) in (404, 410) else "error"
    except Exception:  # noqa: BLE001
        log.warning("alerts: push failed", exc_info=True)
        return "error"


def _sub_of(item: dict) -> dict:
    return json.loads(item["subscription"])


ARRIVAL_PUSH_HOURS = 5   # send the "storm is coming" notification this many hours ahead


def check_alerts(state) -> int:
    """Look at the weather for every subscribed ZIP and push once per change. Returns pushes sent."""
    sent, by_zip = 0, defaultdict(list)
    for item in ALERTS.all():
        by_zip[item["zip"]].append(item)
    for zip_code, items in by_zip.items():
        loc = resolve(state.store, f"ZIP-{zip_code}")
        if loc is None or loc.lat is None:
            continue
        wx = engines.live_weather(state.weather_model, loc.lat, loc.lon)
        if wx["fallback"]:
            continue
        events = sorted(a["event"] for a in (wx["alerts"] or []))
        sev = wx["severity"]

        # "Heads up": the storm is about to arrive. Sent once per storm type per day, from 5 hours before.
        soon = wx["arrival_hours"]
        if soon is not None and soon <= ARRIVAL_PUSH_HOURS and wx["storm_type"] != "none":
            arrival_key = f"{time.strftime('%Y-%m-%d')}|{wx['storm_type']}"
            heads_up = f"{wx['storm_type_label']} arriving in about {soon} hour{'s' if soon != 1 else ''}." if soon > 0                 else f"{wx['storm_type_label']} is arriving now."
            for item in items:
                if item.get("arrival_key") == arrival_key:
                    continue
                result = push(_sub_of(item), {"title": f"Storm arriving: ZIP {zip_code}",
                                              "body": heads_up + " Charge your phone and open the app for tips.", "url": "/"})
                if result == "gone":
                    ALERTS.delete(item["id"])
                elif result == "ok":
                    item["arrival_key"] = arrival_key
                    ALERTS.put(item)
                    sent += 1
        if sev < 3 and not events:
            continue
        key = f"{time.strftime('%Y-%m-%d')}|{sev if sev >= 3 else 0}|{','.join(events)}"
        if events:
            body = f"{events[0]}{' and more' if len(events) > 1 else ''}. Open the app for what to do."
        else:
            body = (f"{wx['severity_label']} expected {wx['peak_day']}. Chance of a damaging day: {wx['chance_pct']}%. "
                    "Open the app for your outage risk.")
        for item in items:
            if item.get("last_key") == key:
                continue
            result = push(_sub_of(item), {"title": f"Storm alert for ZIP {zip_code}", "body": body, "url": "/"})
            if result == "gone":
                ALERTS.delete(item["id"])
            elif result == "ok":
                ALERTS.put({**item, "last_key": key})
                sent += 1
    return sent


async def _alert_loop():
    while True:
        await asyncio.sleep(ALERT_CHECK_SECONDS)
        try:
            n = await asyncio.to_thread(check_alerts, STATE)
            if n:
                log.info("alerts: sent %d notification(s)", n)
        except Exception:  # noqa: BLE001
            log.exception("alerts: check failed")


@app.get("/alerts/config", response_model=AlertConfig)
def alerts_config():
    return {"available": True, "public_key": _public_key()}


@app.post("/alerts/subscribe", response_model=AlertSubscribed)
def alerts_subscribe(req: AlertSubscribeRequest, request: Request):
    t0, rid = time.time(), new_request_id()
    if _rate_limited(request, limit=10, bucket="subscribe"):
        raise HTTPException(429, "Too many requests. Please wait a minute and try again.")
    if resolve(_state().store, f"ZIP-{req.zip}") is None:
        raise HTTPException(404, "We don't have outage data for that ZIP code yet.")
    if not _valid_endpoint(req.subscription.endpoint):
        raise HTTPException(422, "That does not look like a phone push address.")
    sub_id = hashlib.sha256(req.subscription.endpoint.encode()).hexdigest()[:32]
    ALERTS.put({"id": sub_id, "zip": req.zip, "created": int(time.time()), "last_key": "",
                "subscription": json.dumps(req.subscription.model_dump())})
    audit("/alerts/subscribe", rid, f"ZIP-{req.zip}", None, None, t0)
    return {"id": sub_id, "zip": req.zip}


@app.post("/alerts/test", response_model=AlertTestResult)
def alerts_test(req: AlertIdRequest, request: Request):
    """Send one notification right now, so a customer can see exactly what an alert looks like."""
    if _rate_limited(request, limit=5, bucket="test"):
        raise HTTPException(429, "Too many requests. Please wait a minute and try again.")
    item = ALERTS.get(req.id)
    if item is None:
        raise HTTPException(404, "Turn on phone alerts first.")
    result = push(_sub_of(item), {"title": "Test alert from Storm Watch",
                                  "body": "This is what a storm alert looks like. Nothing is wrong right now.", "url": "/"})
    if result == "gone":
        ALERTS.delete(req.id)
        return {"sent": False, "detail": "This phone turned alerts off. Turn them on again."}
    return {"sent": result == "ok", "detail": "Sent. Check your phone." if result == "ok" else "The push service did not accept it. Try again."}


@app.post("/alerts/unsubscribe")
def alerts_unsubscribe(req: AlertIdRequest):
    ALERTS.delete(req.id)
    return {"ok": True}


# The website (built by Docker). Must come last so API routes win.
_static = FsPath(os.getenv("STATIC_DIR", str(data.ROOT / "dist")))
if (_static / "index.html").exists():
    app.mount("/", StaticFiles(directory=_static, html=True), name="web")
