"""Real private socket framing with synthetic measurement, not Docker/reader proof (CI only)."""

from __future__ import annotations

import json
import socket
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from accessforge_build_worker.artifact_probe import ArtifactProbe
from accessforge_build_worker.candidate_gateway import CandidateEndpointBinding, CandidateGateway
from accessforge_build_worker.sandbox import DaemonBinding


def test_private_capability_measures_fresh_and_cleans_its_exact_socket() -> None:
    calls: list[int] = []
    binding = CandidateEndpointBinding(
        task_id=str(uuid.uuid4()),
        artifact_digest="a" * 64,
        runtime_policy_digest="b" * 64,
        candidate_id="c" * 64,
        driver_id="d" * 64,
        image_id="sha256:" + "e" * 64,
        daemon=DaemonBinding("unix:///tmp/fixture-only.sock", "fixture-daemon"),
    )

    def observe() -> dict[str, Any]:
        calls.append(1)
        return {
            "taskId": binding.task_id,
            "candidateId": binding.candidate_id,
            "imageId": binding.image_id,
            "daemonId": binding.daemon.daemon_id,
            "artifactDigest": binding.artifact_digest,
            "artifactTreeDigest": "f" * 64,
            "observedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "meaning": "DEPLOYED_FILESYSTEM_MEASUREMENT_NOT_EXECUTION_ATTESTATION",
        }

    with (
        tempfile.TemporaryDirectory(prefix="afp-") as private,
        CandidateGateway(
            binding=binding,
            nonce="fixture-probe-nonce",
            transport=lambda *args: {},
            observe_artifact=observe,
        ) as gateway,
    ):
        with ArtifactProbe(gateway, private_directory=Path(private)) as probe:
            reference = probe.reference()
            path = Path(reference["socketPath"])
            assert path.stat().st_mode & 0o077 == 0
            for token in ("0" * 64, reference["token"], reference["token"]):
                request_id = uuid.uuid4().hex
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(5)
                    client.connect(str(path))
                    client.sendall(
                        json.dumps(
                            {
                                "protocol": reference["protocol"],
                                "requestId": request_id,
                                "token": token,
                            }
                        ).encode()
                        + b"\n"
                    )
                    response = bytearray()
                    while chunk := client.recv(8192):
                        response.extend(chunk)
                if token != reference["token"]:
                    assert not response and not calls
                else:
                    value = json.loads(response)
                    assert set(value) == {"protocol", "requestId", "observation"}
                    assert value["requestId"] == request_id
                    assert value["observation"]["artifactDigest"] == binding.artifact_digest
            assert len(calls) == 2  # A repeated read measures again; no cached launch receipt.
        assert not path.exists() and not path.parent.exists()
