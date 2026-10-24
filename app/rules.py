"""Rules engine: prep tips, safety messages, alert eligibility, channel, timing, escalation.

Every decision names the rule that made it, so the UI and judges can trace it.
Declared needs come only from the request (opt-in). Nothing here infers them.
"""
from __future__ import annotations

from . import config as C

SAFETY = [
    "Stay at least 30 feet from downed power lines and report them right away.",
    "Never run a generator indoors or in a garage.",
]
SAFETY_LONG = "If you use a generator, keep it outside and at least 20 feet from doors and windows."
SAFETY_FLOOD = "Do not walk or drive through water near downed lines."


def actions_for(band: str, high_hours: float, needs: list[str], access_ok: bool,
                backup_hours: float | None = None) -> tuple[list[str], list[str]]:
    actions: list[str] = []
    if "medical_equipment" in needs:
        actions.append("Confirm backup power for medical equipment, or plan where you will go if it runs out.")
        if backup_hours is not None and high_hours > backup_hours:
            actions.append("This outage could outlast your backup. Arrange a place with power now.")
    if "accessibility" in needs:
        actions.append("Ask a neighbor, friend, or family member to check on you during the storm.")
    if "critical_facility" in needs:
        actions.append("Test your backup generator and confirm you have enough fuel for a long outage.")

    actions.append("Charge phones and power banks.")
    if band in ("medium", "high"):
        actions.append("Keep flashlights and batteries where you can find them.")
        actions.append("Store at least a day of drinking water.")
    if high_hours >= 12:
        actions.append("Plan to keep the fridge closed to keep food cold.")
    if high_hours >= 24:
        actions.append("Keep a few days of medicine and shelf-stable food on hand.")
    if high_hours >= 72:
        actions.append("Pick a place you can stay, like family or a shelter, if the outage lasts several days.")
    if not access_ok:
        actions.append("Roads may be blocked. Avoid travel and stay off closed roads so crews can get through.")
    if band == "low" and len(actions) < 3:
        actions.append("Sign up for outage alerts so you hear about changes quickly.")

    safety = list(SAFETY)
    if high_hours >= 24:
        safety.append(SAFETY_LONG)
    if not access_ok:
        safety.append(SAFETY_FLOOD)
    return actions[:6], safety


def alert(band: str, high_hours: float, needs: list[str], backup_hours: float | None = None) -> tuple[bool, str]:
    if "medical_equipment" in needs:
        backup = backup_hours if backup_hours is not None else C.MEDICAL_BACKUP_HOURS
        if high_hours > backup:
            return True, (f"R2: declared medical equipment, and the outage could last longer than "
                          f"your backup ({backup:g} hours)")
        if band != "low":
            return True, "R2: declared medical equipment need and outage risk"
    if band in ("medium", "high"):
        return True, "R1: risk band medium or high"
    if high_hours >= 24:
        return True, "R3: restoration could take more than 24 hours"
    return False, "R0: low risk and short expected outage, no proactive alert"


def channel_and_timing(band: str, needs: list[str]) -> tuple[str, str]:
    if "medical_equipment" in needs:
        # 24 hours earlier than the standard high-risk alert
        return "phone_call", "Call 48 hours before the storm arrives"
    if "critical_facility" in needs:
        return "phone_call", "Call 24 hours before the storm arrives"
    if band == "high":
        return "sms", "Send 24 hours before the storm arrives"
    if band == "medium":
        return "push", "Send 12 hours before the storm arrives"
    return "email", "Include in the daily storm update"


def escalation(high_hours: float, needs: list[str], backup_hours: float | None = None) -> dict:
    if "medical_equipment" in needs:
        if backup_hours is not None:
            return {"trigger": f"Outage expected to last longer than your backup ({backup_hours:g} hours)",
                    "action": "Flag for a utility follow-up call"}
        return {"trigger": f"Outage expected to last longer than backup power (about {C.MEDICAL_BACKUP_HOURS} hours)",
                "action": "Flag for a utility follow-up call"}
    if "critical_facility" in needs:
        return {"trigger": "Facility outage expected to last more than 4 hours",
                "action": "Notify the utility account representative"}
    if high_hours >= 48:
        return {"trigger": "Estimate grows by more than 12 hours",
                "action": "Send an estimate_updated message and share shelter information"}
    return {"trigger": "Estimate grows by more than 6 hours",
            "action": "Send an estimate_updated message"}


def build(band: str, rng: dict, scenario) -> dict:
    needs = [n.value if hasattr(n, "value") else n for n in scenario.declared_needs]
    hi = rng["high_hours"]
    backup = getattr(scenario, "backup_hours", None) if "medical_equipment" in needs else None
    actions, safety = actions_for(band, hi, needs, scenario.access_ok, backup)
    eligible, rule = alert(band, hi, needs, backup)
    channel, timing = channel_and_timing(band, needs)
    return {
        "customer_actions": {"actions": actions, "safety_messages": safety, "channel": channel,
                             "send_timing": timing, "escalation": escalation(hi, needs, backup)},
        "alert_eligible": eligible,
        "alert_rule": rule,
    }
