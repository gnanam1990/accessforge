"""Immutable local publication previews; no execution or outbound write authority."""

from typing import Any
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.canonical import canonicalize, digest


class Refused(ValueError):
    pass


def checked(preview: dict[str, Any]) -> str:
    """Verify exact stored bytes semantically, without treating their claims as trusted proof."""
    value = dict(preview)
    expected = value.pop("previewDigest", None)
    if (
        not isinstance(expected, str)
        or value.get("kind") != "GITHUB_CHECK_CREATE_PREVIEW"
        or value.get("requiredApproval") != "GITHUB_PUBLISH"
        or len(canonicalize(value).encode("utf-8")) > 131_072
        or digest(value) != expected
    ):
        raise Refused("original publication preview integrity unavailable")
    return expected


def record(conn: psycopg.Connection[Any], *, preview: dict[str, Any]) -> str:
    """Trusted service only: store its reconstructed preview, never request-body verdicts."""
    preview_digest = checked(preview)
    identity = preview["identity"]
    preview_id = str(uuid4())
    conn.execute(
        "INSERT INTO github_publication_preview(id,workspace_id,binding_id,run_id,"
        "preview_digest,preview) VALUES(%s,%s,%s,%s,%s,%s)",
        (
            preview_id,
            identity["workspaceId"],
            identity["bindingId"],
            identity["runId"],
            preview_digest,
            Jsonb(preview),
        ),
    )
    return preview_id


def read(conn: psycopg.Connection[Any], *, preview_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM github_publication_preview WHERE id=%s FOR SHARE", (preview_id,)
    ).fetchone()
    if row is None or not isinstance(row["preview"], dict):
        raise Refused("publication preview unavailable")
    preview = row["preview"]
    if checked(preview) != row["preview_digest"] or any(
        preview["identity"].get(key) != str(row[column])
        for key, column in (
            ("workspaceId", "workspace_id"),
            ("bindingId", "binding_id"),
            ("runId", "run_id"),
        )
    ):
        raise Refused("publication preview identity differs")
    return dict(row)
