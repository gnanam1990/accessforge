"""Validate exact host-observed profile fields before retaining or hashing them."""

from __future__ import annotations

import re
from typing import Any

from .identity import RunnerProfile


def observed_profile(value: Any) -> RunnerProfile:
    fields = {
        "platform",
        "readerName",
        "readerVersion",
        "browserName",
        "browserVersion",
        "locale",
        "keyboardLayout",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or any(
            not isinstance(v, str)
            or not v.strip()
            or re.fullmatch(r"[A-Za-z0-9 ._():-]{1,128}", v) is None
            for v in value.values()
        )
        or value["platform"] != "darwin"
        or value["readerName"] != "VoiceOver"
        or value["browserName"] != "Safari"
    ):
        raise ValueError("closed observed VoiceOver/Safari profile required")
    return RunnerProfile(
        platform=value["platform"],
        reader_name=value["readerName"],
        reader_version=value["readerVersion"],
        browser_name=value["browserName"],
        browser_version=value["browserVersion"],
        locale=value["locale"],
        keyboard_layout=value["keyboardLayout"],
    )
