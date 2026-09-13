"""Export an existing human decision for private native provisioning, never grant authority."""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from .client import AccessForgeClient


class ProvisioningRefused(ValueError):
    pass


def _uuid(value: Any) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ProvisioningRefused("canonical provisioning identity required")
    return value


def _expiry(value: Any) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z", value
    ):
        raise ProvisioningRefused("canonical consent expiry required")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def read_provisioning_reference(
    client: AccessForgeClient, *, workspace_id: str, run_id: str, runner_id: str
) -> dict[str, Any]:
    """Three fresh human reads; native startup must still authenticate and recheck atomically."""
    for value in (workspace_id, run_id, runner_id):
        _uuid(value)
    run = client.call("get_run", workspace_id=workspace_id, run_id=run_id)
    # The scope route requires infrastructure-operator permission and a current execution seal.
    scope = client.call(
        "review_reader_startup_scope",
        workspace_id=workspace_id,
        run_id=run_id,
        params={"runnerId": runner_id},
    )
    grant = client.call("inspect_reader_startup_consent", workspace_id=workspace_id, run_id=run_id)
    if not all(isinstance(row, dict) for row in (run, scope, grant)):
        raise ProvisioningRefused("provisioning records unavailable")
    if (
        run.get("runId") != run_id
        or scope.get("runId") != run_id
        or grant.get("runId") != run_id
        or scope.get("runnerId") != runner_id
        or grant.get("runnerId") != runner_id
        or run.get("status") not in {"QUEUED", "LEASED", "RUNNING"}
        or "cancellationRequestedAt" not in run
        or run.get("cancellationRequestedAt") is not None
        or run.get("quarantined") is not False
        or type(scope.get("revision")) is not int
        or type(run.get("revision")) is not int
        or run.get("revision") != scope["revision"]
        or scope.get("meaning") != "REVIEW_SCOPE_ONLY_NOT_STARTUP_CONSENT"
        or grant.get("meaning") != "STORED_OPERATOR_STARTUP_CONSENT_NOT_PHYSICAL_PROOF"
        or "revokedAt" not in grant
        or grant["revokedAt"] is not None
        or "boundSessionId" not in grant
        or grant["boundSessionId"] is not None
        or run.get("manifestDigest") != scope.get("manifestDigest")
    ):
        raise ProvisioningRefused("run or consent is stale, revoked, already bound or mismatched")
    fields = ("manifestDigest", "desktopSessionKey", "runnerProfileDigest", "effectsDigest")
    for field in fields:
        if (
            not isinstance(scope.get(field), str)
            or not re.fullmatch(r"[0-9a-f]{64}", scope[field])
            or grant.get(field) != scope[field]
        ):
            raise ProvisioningRefused("consent does not match the reviewed native scope")
    expiry = _expiry(grant.get("expiresAt"))
    if expiry <= datetime.now(UTC) or expiry > _expiry(scope.get("maximumExpiresAt")):
        raise ProvisioningRefused("consent expiry is outside current execution authority")
    return {
        "schemaVersion": 1,
        "workspaceId": workspace_id,
        "runId": run_id,
        "runnerId": runner_id,
        "consentExpiresAt": grant["expiresAt"],
        "scope": {
            "consentId": _uuid(grant.get("consentId")),
            **{key: grant[key] for key in fields},
        },
        "meaning": "OPERATOR_CONSENT_REFERENCE_NOT_EXECUTION_AUTHORITY",
    }


def write_private_reference(path: Path, reference: dict[str, Any]) -> None:
    """Create-only in an existing canonical private directory; never overwrite or chmod user data.

    Failed writes remain for explicit reconciliation. This cooperative owner boundary does not
    protect against malicious same-user/root processes or an operator replacing the storage tree.
    """
    if (
        os.name != "posix"
        or not path.is_absolute()
        or path.parent.resolve(strict=True) != path.parent
    ):
        raise ProvisioningRefused("an absolute canonical private output directory is required")
    payload = (json.dumps(reference, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > 8192:
        raise ProvisioningRefused("provisioning reference exceeds its bound")
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd: int | None = None
    try:
        info = os.fstat(parent)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ProvisioningRefused("output directory must be private and owned by this operator")
        fd = os.open(
            path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise OSError("provisioning write incomplete")
            offset += written
        os.fsync(fd)
        os.fsync(parent)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)
