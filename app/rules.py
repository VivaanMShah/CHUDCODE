"""Customer tips, safety messages, alert eligibility, and contact timing.

Each function is one decision. Edit its plain if/else rules directly.
"""
from __future__ import annotations

from . import data as C

# ============================================================================
# 3. RULES
#
# Plain if-this-then-that rules. Each decision names the rule that made it (R0, R1, ...) so you can trace
# why someone was or was not alerted. Needs such as medical equipment come ONLY from what the customer
# chose to tell us. Nothing is guessed.
# ============================================================================

SAFETY_MESSAGES = [
    "Stay at least 30 feet from downed power lines and report them right away.",
    "Never run a generator indoors or in a garage.",
]


def _needs_of(scenario) -> list[str]:
    """The needs the customer declared, as plain text."""
    return [need.value if hasattr(need, "value") else need for need in scenario.declared_needs]


def tips_and_safety(band: str, longest_hours: float, needs: list[str], backup_hours: float | None) -> tuple[list[str], list[str]]:
    """What to do (up to 6 tips) and safety messages, based on risk band, how long it could last, and declared needs."""
    tips = []
    if "medical_equipment" in needs:
        tips.append("Confirm backup power for medical equipment, or plan where you will go if it runs out.")
        if backup_hours is not None and longest_hours > backup_hours:
            tips.append("This outage could outlast your backup. Arrange a place with power now.")
    if "accessibility" in needs:
        tips.append("Ask a neighbor, friend, or family member to check on you during the storm.")
    if "critical_facility" in needs:
        tips.append("Test your backup generator and confirm you have enough fuel for a long outage.")

    tips.append("Charge phones and power banks.")
    if band in ("medium", "high"):
        tips.append("Keep flashlights and batteries where you can find them.")
        tips.append("Store at least a day of drinking water.")
    if longest_hours >= 12:
        tips.append("Plan to keep the fridge closed to keep food cold.")
    if longest_hours >= 24:
        tips.append("Keep a few days of medicine and shelf-stable food on hand.")
    if longest_hours >= 72:
        tips.append("Pick a place you can stay, like family or a shelter, if the outage lasts several days.")
    if band == "low" and len(tips) < 3:
        tips.append("Sign up for outage alerts so you hear about changes quickly.")

    safety = list(SAFETY_MESSAGES)
    if longest_hours >= 24:
        safety.append("If you use a generator, keep it outside and at least 20 feet from doors and windows.")
    return tips[:6], safety


def alert_decision(band: str, longest_hours: float, needs: list[str], backup_hours: float | None) -> tuple[bool, str]:
    """Should this customer get a proactive alert? Returns (yes or no, the rule that decided)."""
    if "medical_equipment" in needs:
        backup = backup_hours if backup_hours is not None else C.MEDICAL_BACKUP_HOURS
        if longest_hours > backup:
            return True, f"R2: declared medical equipment, and the outage could last longer than your backup ({backup:g} hours)"
        if band != "low":
            return True, "R2: declared medical equipment need and outage risk"
    if band in ("medium", "high"):
        return True, "R1: risk band medium or high"
    if longest_hours >= 24 and band != "low":
        return True, "R3: restoration could take more than 24 hours"
    return False, "R0: low risk and short expected outage, no proactive alert"


def channel_and_timing(band: str, needs: list[str]) -> tuple[str, str]:
    """How and when we would reach this customer."""
    if "medical_equipment" in needs:
        return "phone_call", "Call 48 hours before the storm arrives"   # a day earlier than the standard high-risk alert
    if "critical_facility" in needs:
        return "phone_call", "Call 24 hours before the storm arrives"
    if band == "high":
        return "sms", "Send 24 hours before the storm arrives"
    if band == "medium":
        return "push", "Send 12 hours before the storm arrives"
    return "email", "Include in the daily storm update"


def escalation_plan(longest_hours: float, needs: list[str], backup_hours: float | None) -> dict:
    """What triggers a follow-up, and what the follow-up is."""
    if "medical_equipment" in needs:
        limit = f"your backup ({backup_hours:g} hours)" if backup_hours is not None else f"backup power (about {C.MEDICAL_BACKUP_HOURS} hours)"
        return {"trigger": f"Outage expected to last longer than {limit}", "action": "Flag for a utility follow-up call"}
    if "critical_facility" in needs:
        return {"trigger": "Facility outage expected to last more than 4 hours", "action": "Notify the utility account representative"}
    if longest_hours >= 48:
        return {"trigger": "Estimate grows by more than 12 hours", "action": "Send an estimate_updated message and share shelter information"}
    return {"trigger": "Estimate grows by more than 6 hours", "action": "Send an estimate_updated message"}


def build_rules(band: str, restoration_range: dict, scenario) -> dict:
    """Runs all the rules above and returns what the app shows under 'customer_actions'."""
    needs = _needs_of(scenario)
    longest = restoration_range["high_hours"]
    backup = scenario.backup_hours if "medical_equipment" in needs else None   # only used if they told us
    tips, safety = tips_and_safety(band, longest, needs, backup)
    eligible, rule = alert_decision(band, longest, needs, backup)
    channel, timing = channel_and_timing(band, needs)
    return {
        "customer_actions": {"actions": tips, "safety_messages": safety, "channel": channel,
                             "send_timing": timing, "escalation": escalation_plan(longest, needs, backup)},
        "alert_eligible": eligible,
        "alert_rule": rule,
    }
