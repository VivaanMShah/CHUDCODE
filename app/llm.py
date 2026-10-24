"""Amazon Bedrock helper. The model only rewords or explains text built from structured facts.
It never produces numbers: callers check every number it writes (see messages.validate).

Turn it on with LLM_PROVIDER=bedrock. In AWS the task's IAM role supplies credentials. Locally,
credentials come from the environment or .env. Any failure returns None, and callers fall back
to template text.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("llm")

DEFAULT_MODEL = "us.amazon.nova-pro-v1:0"  # Amazon Nova needs no Marketplace subscription. Override with BEDROCK_MODEL_ID.
_client = None


def enabled() -> bool:
    return os.getenv("LLM_PROVIDER", "").lower() == "bedrock" or os.getenv("LLM_POLISH", "").lower() == "bedrock"


def model_id() -> str:
    return os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL)


def _bedrock():
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config

        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        _client = boto3.client("bedrock-runtime", region_name=region,
                               config=Config(connect_timeout=4, read_timeout=15, retries={"max_attempts": 1}))
    return _client


def converse(system: str, user: str, max_tokens: int = 300, temperature: float = 0.2) -> str | None:
    """One Bedrock call. Returns the text, or None when the LLM is off or the call fails."""
    if not enabled():
        return None
    try:
        r = _bedrock().converse(
            modelId=model_id(),
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
        )
        return r["output"]["message"]["content"][0]["text"].strip() or None
    except Exception:  # noqa: BLE001 - never break the app because the model is unavailable
        log.warning("llm: Bedrock call failed, using template text", exc_info=True)
        return None
