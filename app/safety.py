"""Safety layer: strip sensitive fields from every response, audit log, request IDs.

Applied to every response body right before it is returned.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid

from . import config as C

log = logging.getLogger("safety")

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
            C.LOG_DIR.mkdir(parents=True, exist_ok=True)
            _audit.addHandler(logging.FileHandler(C.LOG_DIR / "audit.log", encoding="utf-8"))
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
