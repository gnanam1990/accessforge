"""Inert operator-reference delivery; no database, consent issuance or physical reader."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from accessforge_client import AccessForgeClient
from accessforge_client.reader_provisioning import (
    ProvisioningRefused,
    read_provisioning_reference,
    write_private_reference,
)


@pytest.mark.parametrize("fault", [None, "expired", "bound", "revoked", "digest", "revision"])
def test_export_only_reads_current_matching_unbound_consent(fault: str | None) -> None:
    workspace, run_id, runner = (str(uuid4()) for _ in range(3))
    expiry = (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    identity = {
        "manifestDigest": "a" * 64,
        "desktopSessionKey": "b" * 64,
        "runnerProfileDigest": "c" * 64,
        "effectsDigest": "d" * 64,
    }
    run = {
        "runId": run_id,
        "revision": 1,
        "status": "QUEUED",
        "quarantined": False,
        "cancellationRequestedAt": None,
        "manifestDigest": identity["manifestDigest"],
    }
    scope = {
        **identity,
        "runId": run_id,
        "runnerId": runner,
        "revision": 1,
        "maximumExpiresAt": expiry,
        "meaning": "REVIEW_SCOPE_ONLY_NOT_STARTUP_CONSENT",
    }
    grant = {
        **identity,
        "runId": run_id,
        "runnerId": runner,
        "consentId": str(uuid4()),
        "expiresAt": expiry,
        "revokedAt": None,
        "boundSessionId": None,
        "meaning": "STORED_OPERATOR_STARTUP_CONSENT_NOT_PHYSICAL_PROOF",
    }
    if fault == "expired":
        grant["expiresAt"] = "2020-01-01T00:00:00Z"
    elif fault == "bound":
        grant["boundSessionId"] = str(uuid4())
    elif fault == "revoked":
        grant["revokedAt"] = expiry
    elif fault == "digest":
        grant["manifestDigest"] = "e" * 64
    elif fault == "revision":
        run["revision"] = True
    prefix = f"/v1/workspaces/{workspace}/runs/{run_id}"
    replies = {
        prefix: run,
        prefix + "/reader-startup-consent/scope": scope,
        prefix + "/reader-startup-consent": grant,
    }
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        calls.append(request.url.path)
        if request.url.path.endswith("/scope"):
            assert request.url.params["runnerId"] == runner
        return httpx.Response(200, json=replies[request.url.path])

    with AccessForgeClient(
        "https://example.test", transport=httpx.MockTransport(respond)
    ) as client:
        if fault:
            with pytest.raises(ProvisioningRefused):
                read_provisioning_reference(
                    client, workspace_id=workspace, run_id=run_id, runner_id=runner
                )
        else:
            result = read_provisioning_reference(
                client, workspace_id=workspace, run_id=run_id, runner_id=runner
            )
            assert result["scope"] == {"consentId": grant["consentId"], **identity}
            assert result["meaning"] == "OPERATOR_CONSENT_REFERENCE_NOT_EXECUTION_AUTHORITY"
    assert calls == list(replies)


@pytest.mark.skipif(os.name != "posix", reason="native operator provisioning requires POSIX")
def test_private_reference_is_create_only_and_requires_private_parent(tmp_path: Path) -> None:
    parent = tmp_path.resolve()
    parent.chmod(0o700)
    path = parent / "consent.json"
    write_private_reference(path, {"schemaVersion": 1})
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text()) == {"schemaVersion": 1}
    with pytest.raises(FileExistsError):
        write_private_reference(path, {"replacement": True})
    assert json.loads(path.read_text()) == {"schemaVersion": 1}
    parent.chmod(0o755)
    with pytest.raises(ProvisioningRefused):
        write_private_reference(parent / "public.json", {})
    assert not (parent / "public.json").exists()
