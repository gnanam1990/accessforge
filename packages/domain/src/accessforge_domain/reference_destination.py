"""Instantiate only the exact reviewed reference-fixture URL template."""

from __future__ import annotations

import re
from urllib.parse import urlsplit


def reference_destination(*, sealed_url: str, origin: str, nonce: str) -> str:
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or (parsed.port is not None and not 1 <= parsed.port <= 65535)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.geturl() != origin
        or re.fullmatch(r"[A-Za-z0-9_-]{16,64}", nonce) is None
        or sealed_url != origin + "/form/FIXTURE"
    ):
        raise ValueError("exact reviewed reference URL template and reserved nonce required")
    # No generic substitutions, URL joins, query strings, redirects or new origins.
    return origin + "/form/" + nonce
