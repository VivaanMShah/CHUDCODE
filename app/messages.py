"""Customer message generator: templates, optional LLM polish, number validator.

The LLM never produces numbers. It may only reword a template built from structured
JSON. Every number in its output is checked against the numbers in that JSON; if any
number doesn't match (or the LLM is off or fails), we return the template text.

LLM polish is off unless LLM_POLISH=bedrock is set and boto3 + AWS credentials exist.
"""
from __future__ import annotations

import json
import logging
import os
import re

log = logging.getLogger("messages")

NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _fmt(x: float) -> str:
    return f"{x:.0f}" if abs(x - round(x)) < 0.05 else f"{x:.1f}"


def add_display_units(facts: dict) -> dict:
    """Long outages read better in days. Day values are added to the facts so they validate."""
    if facts["high_hours"] >= 48:
        facts["low_days"] = max(1, round(facts["low_hours"] / 24))
        facts["high_days"] = max(facts["low_days"] + 1, round(facts["high_hours"] / 24))
    return facts


def _span(facts: dict) -> str:
    if "high_days" in facts:
        return f"about {facts['low_days']} to {facts['high_days']} days"
    return f"{_fmt(facts['low_hours'])} to {_fmt(facts['high_hours'])} hours"


def template(message_type: str, facts: dict) -> str:
    span = _span(facts)
    band = facts["risk_band"]
    if message_type == "pre_storm":
        lead = {
            "high": "A strong storm is coming and outages are likely in your area.",
            "medium": "A storm is coming and outages are possible in your area.",
            "low": "A storm is coming. Outages in your area are not likely, but please be ready.",
        }[band]
        return (f"{lead} If you lose power, our current estimate is {span} to restore it. "
                f"{facts['top_action']}")
    if message_type == "outage_confirmed":
        return (f"We've confirmed an outage in your area. Our current estimate is {span}. "
                "We'll update you if this changes.")
    if message_type == "estimate_updated":
        return (f"Update on your outage: our new estimate is {span}. "
                "Crews are working on repairs in your area. Thank you for your patience.")
    if message_type == "restoration_complete":
        return ("Power has been restored in your area. If your power is still out, please report it "
                "so we can send a crew. Stay clear of any downed lines.")
    raise ValueError(message_type)


def numbers_in(text: str) -> list[str]:
    return [n.replace(",", "") for n in NUM_RE.findall(text)]


def allowed_numbers(facts: dict) -> set[str]:
    out: set[str] = set()

    def walk(v):
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            out.update({_fmt(v), str(v), f"{v:.1f}", f"{v:.0f}"})
        elif isinstance(v, str):
            out.update(numbers_in(v))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(facts)
    return out


def validate(text: str, facts: dict) -> bool:
    """True when every number in text appears in the structured facts."""
    ok = allowed_numbers(facts)
    return all(n in ok for n in numbers_in(text))


def _llm_reword(text: str, facts: dict) -> str | None:
    if os.getenv("LLM_POLISH", "").lower() != "bedrock":
        return None
    try:
        import boto3  # optional dependency

        client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))
        prompt = ("Reword this utility customer message to sound warm and clear. Keep it under 60 words. "
                  "Do not add, remove, or change any numbers. Reply with the message only.\n\n"
                  f"Facts (JSON): {json.dumps(facts)}\n\nMessage: {text}")
        resp = client.converse(
            modelId=os.getenv("LLM_MODEL_ID", "anthropic.claude-haiku-4-5"),
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 200, "temperature": 0.3},
        )
        return resp["output"]["message"]["content"][0]["text"].strip()
    except Exception:  # noqa: BLE001 - polish is optional; template is the fallback
        log.warning("messages: LLM polish failed, using template", exc_info=True)
        return None


def generate(message_type: str, facts: dict) -> tuple[str, bool, str]:
    """Return (text, validated, source) where source is 'template' or 'llm'."""
    facts = add_display_units(dict(facts))
    base = template(message_type, facts)
    polished = _llm_reword(base, facts)
    if polished and validate(polished, facts):
        return polished, True, "llm"
    if polished:
        log.warning("messages: LLM output had unmatched numbers, using template")
    return base, validate(base, facts), "template"
