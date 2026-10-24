"""Shared helpers for the AWS scripts. Credentials come from .env or the environment and are never printed."""
import os
import re
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
NAME = "outage-concierge"
REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"


def load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2).strip("\"'")


def client(service: str):
    return boto3.client(service, region_name=REGION)


def account_id() -> str:
    return client("sts").get_caller_identity()["Account"]


def say(msg: str) -> None:
    print(msg, flush=True)
