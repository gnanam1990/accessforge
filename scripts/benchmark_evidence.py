"""Attach offline retained-bundle findings to declared benchmark accounting."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from accessforge_evidence import (
    MAX_ARCHIVE_BYTES,
    CheckOutcome,
    KeyProvenance,
    TrustRoot,
    UnsafeArchive,
    read_archive,
    verify_archive,
)
from benchmark_report import _unique_object, read_input, report


def _snapshot(source: Path, target: Path, expected: str) -> bool:
    # Open once: hashing and verification must examine the same retained bytes.
    # NONBLOCK prevents a substituted FIFO from hanging before the regular-file check.
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("retained reference is not a bounded regular file")
        total = 0
        hasher = hashlib.sha256()
        with target.open("xb") as snapshot:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise ValueError("retained reference grew beyond the limit")
                hasher.update(chunk)
                snapshot.write(chunk)
    return hasher.hexdigest() == expected


def _inspect(
    row: dict[str, Any],
    directory: Path,
    trust_root: TrustRoot | None,
) -> dict[str, Any]:
    expected = row["bundleDigest"]
    if expected is None:
        return {"status": "NO_REFERENCE"}
    try:
        with tempfile.TemporaryDirectory(prefix="accessforge-benchmark-") as temporary:
            snapshot = Path(temporary) / "bundle.zip"
            if not _snapshot(directory / f"{expected}.zip", snapshot, expected):
                return {"status": "DIGEST_MISMATCH"}
            verification = verify_archive(str(snapshot), trust_root=trust_root)
            members = read_archive(str(snapshot))
            document = json.loads(members["bundle.json"], object_pairs_hook=_unique_object)
            manifest = document["manifest"]
            # Signed canonical document parsing is strict too: ambiguous duplicate keys
            # cannot be promoted into a successful readable/signed comparison.
            json.loads(members["manifest.canonical.json"], object_pairs_hook=_unique_object)
            json.loads(members["attestation.json"], object_pairs_hook=_unique_object)
            machine = document["machineOutcome"]
            if not isinstance(manifest, dict) or not isinstance(machine, dict):
                raise ValueError("invalid bundle documents")
            # Match against the signed field, not only the human-readable copy. Keep this
            # explicit even when the offline verifier also checks their consistency.
            matches = (
                row["outcome"] is not None
                and row["outcome"] == manifest.get("outcome") == machine.get("outcome")
                and bool(manifest.get("evaluatorVersion"))
                and manifest.get("evaluatorVersion") == machine.get("evaluatorVersion")
            )
            counts = Counter(f.outcome for f in verification.findings)
            signature_matches = any(
                finding.check == "attestation.signature" and finding.outcome == CheckOutcome.PASSED
                for finding in verification.findings
            )
            return {
                "status": "INSPECTED",
                "archiveDigestMatches": True,
                "integrityIntact": verification.integrity_intact,
                "independentTrustRoot": verification.independently_trusted,
                "signatureMatches": signature_matches,
                "signatureVerifiedWithIndependentKey": (
                    verification.independently_trusted and signature_matches
                ),
                "declaredOutcomeMatchesManifest": matches,
                "findingCounts": {str(outcome): counts[outcome] for outcome in CheckOutcome},
                "cohortIdentityVerified": False,
                "independentExecutionVerified": False,
            }
    except FileNotFoundError:
        return {"status": "MISSING"}
    except OSError:
        return {"status": "UNREADABLE"}
    except (UnsafeArchive, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        # Untrusted verifier input can be syntactically JSON but have incompatible shapes.
        # No raw archive data, filename, producer name or error text enters the report.
        return {"status": "INVALID_BUNDLE"}


def retained_report(
    plan_data: Any,
    result_data: Any,
    directory: Path,
    *,
    trust_root: TrustRoot | None = None,
) -> dict[str, Any]:
    result = report(plan_data, result_data)
    if not directory.is_dir():
        raise ValueError("retained bundle directory does not exist")
    counts: Counter[str] = Counter()
    for row in result["rows"]:
        retained = _inspect(row, directory, trust_root)
        row["retainedEvidence"] = retained
        counts[retained["status"]] += 1
    result["retainedEvidence"] = {
        "kind": "OFFLINE_BUNDLE_INSPECTION_NOT_BENCHMARK_ACCEPTANCE",
        "counts": {
            status: counts[status]
            for status in (
                "NO_REFERENCE",
                "MISSING",
                "UNREADABLE",
                "DIGEST_MISMATCH",
                "INVALID_BUNDLE",
                "INSPECTED",
            )
        },
        "trustRootSupplied": trust_root is not None,
    }
    result["limitations"].extend(
        [
            "INSPECTED means checks ran, not passed. Read all findings and binding fields.",
            "Cohort identity, labels, durations and independent repetitions remain unverified.",
            "Intact bytes/signatures do not prove physical events or reader qualification.",
        ]
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--bundles", required=True, type=Path)
    parser.add_argument("--trust-root", type=Path)
    args = parser.parse_args()
    try:
        root = None
        if args.trust_root:
            document = read_input(args.trust_root)
            if not isinstance(document, dict) or set(document) != {"keyId", "issuer", "publicKey"}:
                raise ValueError("invalid trust root")
            if not all(isinstance(value, str) and value for value in document.values()):
                raise ValueError("invalid trust root values")
            root = TrustRoot(
                key_id=document["keyId"],
                issuer=document["issuer"],
                public_key_base64=document["publicKey"],
                provenance=KeyProvenance.EXTERNALLY_SUPPLIED,
            )
        result = retained_report(
            read_input(args.plan),
            read_input(args.results),
            args.bundles,
            trust_root=root,
        )
    except (OSError, ValueError, RecursionError):
        parser.exit(2, "benchmark evidence inputs refused; check private operator inputs\n")
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
