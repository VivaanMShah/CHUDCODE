"""Connect the model steps to the API response.

Start at assess(): location -> risk -> restoration -> confidence -> customer rules.
Edit calculations in risk.py / restoration.py, advice in rules.py, and forecasts
in weather.py. Training lives in train_weather.py. See MODEL_GUIDE.md for examples.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from . import data
from .assistant import generate, validate
from .data import resolve, upstream_band
from .risk import (
    FALLBACK_ANALOG, RiskModel, area_risk, history_confidence,
    model_confidence, sample_confidence, train_risk,
)
from .restoration import estimate_restoration, spread_confidence
from .rules import _needs_of, build_rules
from .weather import live_weather, load_weather_model
from .train_weather import SIGNIFICANT_REPORTS, train_weather

C = data
log = logging.getLogger("engines")


# ============================================================================
# 6. REQUEST FLOW
#
# State holds everything loaded at startup. assess() answers one request using the model modules.
# ============================================================================

LEVELS = {"low": 0, "medium": 1, "high": 2}   # for picking the lowest of several confidence levels


@dataclass
class State:
    store: data.Store             # the loaded data
    model: RiskModel              # the area-exposure model
    data_mode: str                # "live" (real data) or "mock" (data files missing)
    weather_model: dict | None = None


def startup() -> State:
    """Load the data, train the exposure model, and load the weather model. Runs once when the server starts."""
    store = data.load()
    mode = "live"
    if not store.live:
        log.warning("engine: real data unavailable, running in mock mode")
        store = data.mock_store(store.report)
        mode = "mock"
    model = train_risk(store) if mode == "live" else RiskModel()
    log.info("engine: ready in %s mode\n%s", mode, data.schema_report(store))
    return State(store, model, mode, load_weather_model())


def _analog_of(state: State) -> dict:
    """The history tables (or the stand-in if the trained file is missing)."""
    analog = (state.weather_model or {}).get("analog") or {}
    if analog.get("exposure_model", "logistic_regression") != state.model.kind:
        return FALLBACK_ANALOG  # never mix tables and rankings from different models
    return analog or FALLBACK_ANALOG


def backtest(state: State) -> dict | None:
    """The exposure model's test results, shown under 'About this estimate'."""
    model = state.model
    if not model.ok:
        return None
    high, low = model.hit_rates.get("high", {}), model.hit_rates.get("low", {})
    return {
        "event_label": (state.store.reference_event or {}).get("label", "largest storm on record"),
        "areas": model.n_train,
        "auc": round(model.auc, 3),
        "high_band_hit_rate": None if high.get("rate") is None else round(high["rate"], 2),
        "low_band_hit_rate": None if low.get("rate") is None else round(low["rate"], 2),
        "high_band_areas": high.get("areas", 0),
        "low_band_areas": low.get("areas", 0),
    }


def _assumptions(state: State, scenario) -> list[str]:
    """Every assumption behind the numbers, in plain English. Shown to the customer."""
    model = state.model
    big_storm = (state.store.reference_event or {}).get("label", "largest storm on record")
    analog = _analog_of(state)

    notes = [
        "Storm strength (1 to 5) comes from the forecast. A small model scores how likely a day is to cause real damage "
        f"({SIGNIFICANT_REPORTS} or more damage reports). Strengths 2 to 5 are the top 12%, 4%, 1%, and 0.3% of days since late 2022.",
        "Your risk is what actually happened on days of this strength in the utility's records: the share of areas with "
        "storm damage within about 2 days, for areas as exposed as yours.",
    ]
    if model.ok:
        name = model.kind.replace("_", " ")
        notes.append(f"Area exposure uses {name} with line miles, customers served, and pre-storm damage history. "
                     f"Five-fold testing held out areas within the {big_storm} "
                     f"(AUC {model.auc:.2f} across {model.n_train} areas), not a separate future storm.")
        if model.kind == "random_forest":
            notes.append("Area-factor explanations compare the score with one input replaced by its training median. "
                         "These comparisons are not additive or proof of cause and effect.")
        high, low = model.hit_rates.get("high", {}), model.hit_rates.get("low", {})
        if high.get("rate") is not None and low.get("rate") is not None:
            notes.append(f"Held-out check: of {high['areas']} areas it rated high for that storm, {round(high['rate'] * 100)}% had damage; "
                         f"of {low['areas']} rated low, {round(low['rate'] * 100)}% did.")
    else:
        notes.append("Area exposure is unavailable, so a middle value is used.")

    notes += [
        f"Restoration uses real repair times: {analog.get('repair_source', 'the damage records')}. Dates in the records have "
        "day precision, so each time is spread within its day.",
        "Repair dates for other storms looked unreliable (many equal the date the data was pulled), so only that storm is used.",
        "How many damaged items sit between you and the substation is an assumption: about 1 on a calm day, up to 4 in a severe storm. "
        "Your power returns when the slowest of those repairs is done.",
    ]

    if "medical_equipment" in _needs_of(scenario):
        if scenario.backup_hours is not None:
            notes.append(f"You told us your backup power lasts {scenario.backup_hours:g} hours. We compare that to the high end of the range.")
        else:
            notes.append(f"Backup power for medical equipment is assumed to last {C.MEDICAL_BACKUP_HOURS} hours unless you tell us otherwise.")
    if scenario.damaged_poles > 0:
        notes.append("Each damaged pole you enter adds a repair drawn from real pole repair times, and damage is assumed.")
    return notes


def _limits(state: State, is_zip: bool) -> list[str]:
    """What these numbers cannot tell you. Shown to the customer."""
    notes = [
        "Estimates are model output, not confirmed restoration times from the utility.",
        f"The range covers the 10th to 90th percentile of {C.MC_SAMPLES} simulations; actual times can fall outside it.",
        "The records cover a few storms and one large one, so rates for strong storms rest on few days. "
        "Strengths 4 and 5 share one row of history (15 days).",
        "The weather model ranks damaging days better than chance, but it misses many small damage days.",
        "Locations are neighborhood-level areas (one circuit), not individual homes.",
        "Damage records have day-level dates only, so past repair times are approximate.",
    ]
    if is_zip:
        notes.append("A ZIP code is served by several circuits. The estimate is for a typical one, and ZIP borders are approximate.")
    if state.model.ok:
        notes.append(f"The area-exposure model learned from {state.model.n_train} areas and one major storm; "
                     "other storm types may behave differently.")
    if state.data_mode == "mock":
        notes.append("Mock data is in use; numbers are illustrative only.")
    return notes


def _confirmed_facts(state: State, feats: dict, is_zip: bool) -> list[dict]:
    """Facts that come straight from the utility's records (nothing predicted goes here)."""
    if state.data_mode != "live":
        return []
    facts = []
    reports = int(feats.get("hist_records") or 0)
    storms = int(feats.get("hist_events") or 0)
    where = "typical circuit serving this ZIP" if is_zip else "circuit here"
    if reports:
        facts.append({"fact": "past_damage_reports", "value": reports, "source": "ARCOS damage assessment records",
                      "text": f"Utility records show {reports} storm damage reports on a {where} across {storms} past "
                              f"storm{'s' if storms != 1 else ''}."})
    big_storm = state.store.reference_event
    in_big_storm = int(feats.get("ref_records") or 0)
    if big_storm and in_big_storm:
        facts.append({"fact": "reference_storm_damage", "value": in_big_storm, "source": "ARCOS damage assessment records",
                      "text": f"{in_big_storm} of those reports came from the {big_storm['label']}."})
    still_open = int(feats.get("open_damage") or 0)
    if still_open:
        facts.append({"fact": "open_damage_reports", "value": still_open, "source": "ARCOS damage assessment records",
                      "text": f"{still_open} past damage report{'s are' if still_open != 1 else ' is'} not yet marked repaired."})
    return facts


def _confidence(state: State, feats: dict, risk: dict, restoration: dict) -> tuple[str, str, dict]:
    """How sure are we? The answer is the LOWEST of four checks, each from something real:
        model    - how well the exposure model did in its test
        sample   - how many similar days are in the records
        history  - how much damage history this area has
        spread   - how wide the restoration range is
    Returns (level, a sentence explaining it, the four levels)."""
    checks = {
        "model": model_confidence(state.model),
        "sample": sample_confidence(risk["similar_days"]),
        "history": history_confidence(feats),
        "spread": spread_confidence(restoration["spread"]),
    }
    level = min(checks.values(), key=LEVELS.get)

    reports = int(feats.get("hist_records") or 0)
    facts = []
    if state.model.ok:
        best = state.model.hit_rates.get("high", {}).get("rate")
        text = (f"model backtest AUC {state.model.auc:.2f} on the "
                f"{(state.store.reference_event or {}).get('label', 'largest storm')}")
        if best is not None:
            text += f" ({round(best * 100)}% of areas it rated high had damage)"
        facts.append(text)
    facts.append(f"{risk['similar_days']} similar days in the records")
    facts.append(f"{reports} past damage report{'s' if reports != 1 else ''} in this area")
    facts.append(f"restoration range spread {restoration['spread']:.1f} times the median")
    return level, f"Confidence is {level}: " + "; ".join(facts) + ".", checks


def _fallback(state: State, level: str, checks: dict) -> tuple[bool, str | None]:
    """Whether to show the "we can't predict this reliably" state, and why."""
    if state.data_mode == "mock":
        return True, "Real utility data is not loaded, so these numbers are illustrative only."
    if level != "low":
        return False, None
    if checks["sample"] == "low":
        return True, "We have too few days like this on record to rate it reliably. Treat the estimate as a rough guide."
    if checks["history"] == "low":
        return True, ("We have no past storm damage records for this area, so we can't check the "
                      "model here. Treat the estimate as a rough guide.")
    if checks["model"] == "low":
        return True, "The risk model did not pass its backtest, so the estimate is not reliable."
    return True, ("The restoration range is very wide for this scenario, so we can't give a "
                  "reliable time. We'll update you as crews assess damage.")


def _area_summary(state: State, place, feats: dict) -> dict:
    """The coarse, privacy-safe facts about the area that the app may show."""
    chain = feats.get("upstream_chain")
    return {
        "label": place.label,
        "customers_served_band": place.customers_served_band,
        "upstream_device_type": feats.get("upstream_device_type"),
        "customers_on_upstream_device_band": upstream_band(feats.get("customers_on_upstream_device")),
        "critical_facility_nearby": bool(feats.get("critical_facility_nearby")) if state.data_mode == "live" else None,
        "upstream_chain": chain if isinstance(chain, list) else None,
    }


def assess(state: State, location_id: str, scenario, request_id: str) -> dict | None:
    """Answer one request. Returns None if the location does not exist."""
    place = resolve(state.store, location_id)                                           # 1. find the place
    if place is None:
        return None
    feats = place.features
    analog = _analog_of(state)

    risk = area_risk(state.model, analog, feats, scenario.storm_severity, scenario.damaged_poles)   # 2. risk
    restoration = estimate_restoration(analog, scenario)                                            # 3. time
    level, level_reason, checks = _confidence(state, feats, risk, restoration)                      # 4. certainty
    fallback, fallback_reason = _fallback(state, level, checks)
    rules = build_rules(risk["risk_band"], restoration["range"], scenario)                          # 5. rules

    return {                                                                                        # 6. package
        "location_id": location_id,
        "confirmed": _confirmed_facts(state, feats, place.is_zip),
        "model_estimate": {
            "risk_band": risk["risk_band"],
            "risk_score": risk["risk_score"],
            "confidence": level,
            "confidence_reason": level_reason,
            "reasons": risk["reasons"],
            "restoration_range": restoration["range"],
            "factor_deltas": restoration["deltas"],
            "alert_eligible": rules["alert_eligible"],
            "alert_rule": rules["alert_rule"],
            "restoration_histogram": restoration["histogram"],
            "simulations": restoration["simulations"],
            "similar_days": risk["similar_days"],
            "times_normal": risk["times_normal"],
        },
        "customer_actions": rules["customer_actions"],
        "meta": {
            "fallback": fallback,
            "fallback_reason": fallback_reason,
            "assumptions": _assumptions(state, scenario),
            "limits": _limits(state, place.is_zip),
            "data_mode": state.data_mode,
            "request_id": request_id,
            "backtest": backtest(state),
        },
        "area": _area_summary(state, place, feats),
    }


# When each message type is sent (the "before the storm" timing comes from the rules instead).
SEND_TIMING = {
    "outage_confirmed": "Send as soon as the outage is confirmed",
    "estimate_updated": "Send when the estimate changes by more than 2 hours",
    "restoration_complete": "Send when power is restored",
}

# Used instead of the normal text when we are not confident: no numbers, just honesty.
FALLBACK_TEXT = {
    "pre_storm": "A storm is coming. We can't give a reliable outage estimate for your area yet. "
                 "Please charge your phone and keep flashlights handy. We'll update you as we learn more.",
    "outage_confirmed": "We've confirmed an outage in your area. Crews are assessing the damage, and we'll "
                        "send a restoration estimate as soon as we have a reliable one.",
    "estimate_updated": "Crews are still assessing damage in your area. We'll send a restoration estimate "
                        "as soon as we have a reliable one.",
}


def message(state: State, location_id: str, message_type: str, scenario, request_id: str) -> dict | None:
    """The customer message for one stage (before storm, outage confirmed, estimate updated, restored)."""
    answer = assess(state, location_id, scenario, request_id)
    if answer is None:
        return None
    estimate, actions = answer["model_estimate"], answer["customer_actions"]

    # Every number the message may use. The number checker rejects any number not in this list.
    facts = {
        "low_hours": estimate["restoration_range"]["low_hours"],
        "high_hours": estimate["restoration_range"]["high_hours"],
        "median_hours": estimate["restoration_range"]["median_hours"],
        "risk_band": estimate["risk_band"],
        "top_action": actions["actions"][0] if actions["actions"] else "",
    }
    chain = (answer.get("area") or {}).get("upstream_chain") or []
    main_line = next((step for step in chain if step["device_type"] == "recloser"), chain[0] if chain else None)
    if main_line and main_line.get("customers_approx"):
        facts["line_customers"] = main_line["customers_approx"]

    if answer["meta"]["fallback"] and message_type in FALLBACK_TEXT:
        text = FALLBACK_TEXT[message_type]
        validated = validate(text, facts)
    else:
        text, validated, _ = generate(message_type, facts)

    timing = actions["send_timing"] if message_type == "pre_storm" else SEND_TIMING[message_type]
    return {
        "location_id": location_id,
        "message_type": message_type,
        "text": text,
        "channel": actions["channel"],
        "send_timing": timing,
        "validated": validated,
        "meta": answer["meta"],
        "_risk_band": estimate["risk_band"],
    }


if __name__ == "__main__":
    if sys.argv[1:] == ["train-weather"]:
        train_weather()
    else:
        print("usage: python -m app.engines train-weather")
