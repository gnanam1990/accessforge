"""Closed owned-reference endpoint contract, shared by trusted execution and persistence."""

from __future__ import annotations

import re
import uuid
from typing import Any

PROTOCOL = "owned-reference-loopback-v1"
CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'; "
    "sandbox allow-forms allow-scripts allow-same-origin"
)
PLAN_KEYS = frozenset(
    {
        "protocol",
        "path",
        "taskId",
        "artifactDigest",
        "runtimePolicyDigest",
        "candidateId",
        "driverId",
        "imageId",
        "daemonEndpoint",
        "daemonId",
        "contentSecurityPolicy",
        "wallSeconds",
    }
)


def validate_endpoint_plan(plan: dict[str, Any]) -> None:
    if (
        set(plan) not in (PLAN_KEYS, PLAN_KEYS | {"listenOrigin"})
        or plan["protocol"] != PROTOCOL
        or plan["contentSecurityPolicy"] != CSP
    ):
        raise ValueError("endpoint plan differs from the owned protocol")
    if "listenOrigin" in plan:
        validate_endpoint_origin(plan["listenOrigin"])
    if any(not isinstance(plan[key], str) for key in PLAN_KEYS - {"wallSeconds"}):
        raise ValueError("endpoint identity must be text")
    if type(plan["wallSeconds"]) is not int or not 1 <= plan["wallSeconds"] <= 60:
        raise ValueError("endpoint lifetime is out of bounds")
    if not re.fullmatch(r"/form/[A-Za-z0-9_-]{16,64}", plan["path"]):
        raise ValueError("endpoint must name one exact fixture")
    if str(uuid.UUID(plan["taskId"])) != plan["taskId"]:
        raise ValueError("endpoint task identity is not canonical")
    for key in ("artifactDigest", "runtimePolicyDigest", "candidateId", "driverId"):
        if not re.fullmatch(r"[a-f0-9]{64}", plan[key]):
            raise ValueError("invalid endpoint content/process digest")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", plan["imageId"]):
        raise ValueError("invalid endpoint image digest")


def validate_endpoint_origin(origin: str) -> None:
    if not isinstance(origin, str) or not re.fullmatch(
        r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", origin
    ):
        raise ValueError("endpoint origin must be the exact IPv4 loopback listener")
    if int(origin.rsplit(":", 1)[1]) > 65535:
        raise ValueError("invalid endpoint listener port")
