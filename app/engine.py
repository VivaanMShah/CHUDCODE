"""Orchestrates resolver -> risk -> restoration -> rules -> messages for one request."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from . import config as C
from . import data, messages, restoration, risk, rules, weather
from .resolver import resolve, upstream_band

log = logging.getLogger("engine")

LEVELS = {"low": 0, "medium": 1, "high": 2}


@dataclass
class State:
    store: data.Store
    model: risk.RiskModel
    data_mode: str
    weather_model: dict | None = None


def startup() -> State:
    store = data.load()
    mode = "live"
    if not store.live:
        log.warning("engine: real data unavailable, running in mock mode")
        store = data.mock_store(store.report)
        mode = "mock"
    model = risk.train(store) if mode == "live" else risk.RiskModel()
    log.info("engine: ready in %s mode\n%s", mode, data.schema_report(store))
    return State(store, model, mode, weather.load_model())


def backtest(state: State) -> dict | None:
    m = state.model
    if not m.ok:
        return None
    hi, lo = m.hit_rates.get("high", {}), m.hit_rates.get("low", {})
    return {
        "event_label": (state.store.reference_event or {}).get("label", "largest storm on record"),
        "areas": m.n_train,
        "auc": round(m.auc, 3),
        "high_band_hit_rate": None if hi.get("rate") is None else round(hi["rate"], 2),
        "low_band_hit_rate": None if lo.get("rate") is None else round(lo["rate"], 2),
        "high_band_areas": hi.get("areas", 0),
        "low_band_areas": lo.get("areas", 0),
    }


def _assumptions(state: State, scenario) -> list[str]:
    m, ref = state.model, state.store.reference_event or {}
    ref_label = ref.get("label", "largest storm on record")
    h = C.PLACEHOLDER_LABOR_HOURS
    out = []
    if m.ok:
        out.append(f"Risk model: logistic regression on miles of overhead line, customers served, and past "
                   f"damage. Trained on history before the {ref_label} and backtested on it "
                   f"(AUC {m.auc:.2f} across {m.n_train} areas).")
        hi, lo = m.hit_rates.get("high", {}), m.hit_rates.get("low", {})
        if hi.get("rate") is not None and lo.get("rate") is not None:
            out.append(f"Held-out check: of {hi['areas']} areas the model rated high risk for that storm, "
                       f"{round(hi['rate'] * 100)}% had damage; of {lo['areas']} rated low, "
                       f"{round(lo['rate'] * 100)}% did.")
    else:
        out.append("Risk model unavailable; risk uses a neutral baseline adjusted for storm severity.")
    out += [
        f"Storm severity {C.REFERENCE_SEVERITY} is calibrated to the {ref_label}, the largest in the "
        f"utility's damage records. Each level up or down changes the odds of an outage about {math.exp(C.SEVERITY_LOGIT_STEP):.0f} times (an assumption, not fitted to data).",
        f"Expected damage blends this area's damage in the {ref_label} with the system average per mile of line.",
        f"Labor hours per repair are placeholders until the STORMS file is provided: pole {h['pole']}, "
        f"primary wire {h['primary_wire']}, transformer {h['transformer']}, fuse or switch {h['fuse_switch']} crew-hours.",
        f"Travel multiplier {C.PLACEHOLDER_TRAVEL_MULT} and nonproductive multiplier "
        f"{C.PLACEHOLDER_NONPRODUCTIVE_MULT} are placeholders until the indirect labor file is provided.",
        f"Blocked roads add about {round((C.BLOCKED_ROADS_TRAVEL_MULT - 1) * 100)}% to travel and work time.",
        f"Each crew is available {round(C.CREW_AVAILABILITY * 100)}% of the time and works "
        f"{restoration.WORK_HOURS_PER_DAY}-hour shifts.",
    ]
    needs = [n.value if hasattr(n, "value") else n for n in scenario.declared_needs]
    if "medical_equipment" in needs:
        if scenario.backup_hours is not None:
            out.append(f"You told us your backup power lasts {scenario.backup_hours:g} hours. "
                       "We compare that to the high end of the range.")
        else:
            out.append(f"Backup power for medical equipment is assumed to last {C.MEDICAL_BACKUP_HOURS} hours "
                       "unless you tell us otherwise.")
    if scenario.damaged_poles > 0:
        out.append("Other damage (wires, fuses, transformers) is scaled from the pole count using the "
                   f"{ref_label} damage mix.")
    return out


def _limits(state: State, is_zip: bool = False) -> list[str]:
    out = [
        "Estimates are model output, not confirmed restoration times from the utility.",
        f"The range covers the 10th to 90th percentile of {C.MC_SAMPLES} simulations; "
        "actual times can fall outside it.",
        "Locations are neighborhood-level areas (one circuit), not individual homes.",
        "Damage records have day-level dates only, so past repair times are approximate.",
    ]
    if is_zip:
        out.append("A ZIP code is served by several circuits. The estimate is for a typical one, "
                   "and ZIP borders are approximate.")
    if state.model.ok:
        out.append(f"The risk model learned from {state.model.n_train} areas and one major storm; "
                   "other storm types may behave differently.")
    if state.data_mode == "mock":
        out.append("Mock data is in use; numbers are illustrative only.")
    return out


def _confirmed(state: State, feats: dict, is_zip: bool = False) -> list[dict]:
    """Only real utility records. Nothing predicted goes here."""
    if state.data_mode != "live":
        return []
    out = []
    n = int(feats.get("hist_records") or 0)
    ev = int(feats.get("hist_events") or 0)
    if n:
        out.append({"fact": "past_damage_reports", "value": n, "source": "ARCOS damage assessment records",
                    "text": f"Utility records show {n} storm damage reports on a {'typical circuit serving this ZIP' if is_zip else 'circuit here'} across {ev} past "
                            f"storm{'s' if ev != 1 else ''}."})
    ref = state.store.reference_event
    r = int(feats.get("ref_records") or 0)
    if ref and r:
        out.append({"fact": "reference_storm_damage", "value": r, "source": "ARCOS damage assessment records",
                    "text": f"{r} of those reports came from the {ref['label']}."})
    o = int(feats.get("open_damage") or 0)
    if o:
        out.append({"fact": "open_damage_reports", "value": o, "source": "ARCOS damage assessment records",
                    "text": f"{o} past damage report{'s are' if o != 1 else ' is'} not yet marked repaired."})
    return out


def assess(state: State, location_id: str, scenario, request_id: str) -> dict | None:
    loc = resolve(state.store, location_id)
    if loc is None:
        return None
    f = loc.features
    rk = risk.score(state.model, f, scenario)
    rs = restoration.estimate(f, state.store, scenario)
    rng = rs["range"]

    parts = {
        "model": risk.model_confidence(state.model),
        "history": risk.history_confidence(f),
        "spread": restoration.spread_confidence(rs["spread"]),
    }
    confidence = min(parts.values(), key=LEVELS.get)
    n = int(f.get("hist_records") or 0)
    reason_bits = []
    if state.model.ok:
        hr = state.model.hit_rates.get("high", {}).get("rate")
        bit = (f"model backtest AUC {state.model.auc:.2f} on the "
               f"{(state.store.reference_event or {}).get('label', 'largest storm')}")
        if hr is not None:
            bit += f" ({round(hr * 100)}% of areas it rated high had damage)"
        reason_bits.append(bit)
    reason_bits.append(f"{n} past damage report{'s' if n != 1 else ''} in this area")
    reason_bits.append(f"restoration range spread {rs['spread']:.1f} times the median")
    confidence_reason = (f"Confidence is {confidence}: " + "; ".join(reason_bits) + ".")

    fallback, fallback_reason = False, None
    if state.data_mode == "mock":
        fallback, fallback_reason = True, "Real utility data is not loaded, so these numbers are illustrative only."
    elif confidence == "low":
        fallback = True
        if parts["history"] == "low":
            fallback_reason = ("We have no past storm damage records for this area, so we can't check the "
                               "model here. Treat the estimate as a rough guide.")
        elif parts["model"] == "low":
            fallback_reason = "The risk model did not pass its backtest, so the estimate is not reliable."
        else:
            fallback_reason = ("The restoration range is very wide for this scenario, so we can't give a "
                               "reliable time. We'll update you as crews assess damage.")

    rl = rules.build(rk["risk_band"], rng, scenario)
    area = {
        "label": loc.label,
        "customers_served_band": loc.customers_served_band,
        "upstream_device_type": f.get("upstream_device_type"),
        "customers_on_upstream_device_band": upstream_band(f.get("customers_on_upstream_device")),
        "critical_facility_nearby": bool(f.get("critical_facility_nearby")) if state.data_mode == "live" else None,
        "upstream_chain": f.get("upstream_chain") if isinstance(f.get("upstream_chain"), list) else None,
    }
    return {
        "location_id": location_id,
        "confirmed": _confirmed(state, f, loc.is_zip),
        "model_estimate": {
            "risk_band": rk["risk_band"],
            "risk_score": rk["risk_score"],
            "confidence": confidence,
            "confidence_reason": confidence_reason,
            "reasons": rk["reasons"],
            "restoration_range": rng,
            "factor_deltas": rs["deltas"],
            "alert_eligible": rl["alert_eligible"],
            "alert_rule": rl["alert_rule"],
            "restoration_histogram": rs["histogram"],
            "simulations": rs["simulations"],
        },
        "customer_actions": rl["customer_actions"],
        "meta": {
            "fallback": fallback,
            "fallback_reason": fallback_reason,
            "assumptions": _assumptions(state, scenario),
            "limits": _limits(state, loc.is_zip),
            "data_mode": state.data_mode,
            "request_id": request_id,
            "backtest": backtest(state),
        },
        "area": area,
    }


SEND_TIMING = {
    "outage_confirmed": "Send as soon as the outage is confirmed",
    "estimate_updated": "Send when the estimate changes by more than 2 hours",
    "restoration_complete": "Send when power is restored",
}

FALLBACK_TEXT = {
    "pre_storm": "A storm is coming. We can't give a reliable outage estimate for your area yet. "
                 "Please charge your phone and keep flashlights handy. We'll update you as we learn more.",
    "outage_confirmed": "We've confirmed an outage in your area. Crews are assessing the damage, and we'll "
                        "send a restoration estimate as soon as we have a reliable one.",
    "estimate_updated": "Crews are still assessing damage in your area. We'll send a restoration estimate "
                        "as soon as we have a reliable one.",
}


def message(state: State, location_id: str, message_type: str, scenario, request_id: str) -> dict | None:
    a = assess(state, location_id, scenario, request_id)
    if a is None:
        return None
    est, acts = a["model_estimate"], a["customer_actions"]
    facts = {
        "low_hours": est["restoration_range"]["low_hours"],
        "high_hours": est["restoration_range"]["high_hours"],
        "median_hours": est["restoration_range"]["median_hours"],
        "risk_band": est["risk_band"],
        "top_action": acts["actions"][0] if acts["actions"] else "",
    }
    chain = (a.get("area") or {}).get("upstream_chain") or []
    line = next((c for c in chain if c["device_type"] == "recloser"), chain[0] if chain else None)
    if line and line.get("customers_approx"):
        facts["line_customers"] = line["customers_approx"]
    if a["meta"]["fallback"] and message_type in FALLBACK_TEXT:
        text = FALLBACK_TEXT[message_type]
        validated = messages.validate(text, facts)
    else:
        text, validated, _ = messages.generate(message_type, facts)
    timing = acts["send_timing"] if message_type == "pre_storm" else SEND_TIMING[message_type]
    return {
        "location_id": location_id,
        "message_type": message_type,
        "text": text,
        "channel": acts["channel"],
        "send_timing": timing,
        "validated": validated,
        "meta": a["meta"],
        "_risk_band": est["risk_band"],
    }
