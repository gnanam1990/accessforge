"""`accessforge-verify` — the offline verifier as a command.

No account, no network, no database. It takes a bundle and optionally a trust root, prints what it
could establish, and exits nonzero if any integrity check failed.

The trust root is a separate file the reader supplies. That is the whole design of the command: a
verifier that read the key out of the bundle by default would print a green report for a forgery, so
obtaining the key is a step the reader takes deliberately and the report says which case applies.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .signing import KeyProvenance, TrustRoot
from .verifier import verify_archive


def _load_trust_root(path: str) -> TrustRoot:
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    return TrustRoot(
        key_id=str(document["keyId"]),
        issuer=str(document["issuer"]),
        public_key_base64=str(document["publicKey"]),
        # Supplied out of band by the reader, which is what makes the attribution mean something.
        provenance=KeyProvenance.EXTERNALLY_SUPPLIED,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="accessforge-verify",
        description=(
            "Verify an AccessForge evidence bundle offline. Requires no account and no network. "
            "Reports what the bundle establishes and, explicitly, what it does not."
        ),
    )
    parser.add_argument("bundle", help="path to the bundle archive")
    parser.add_argument(
        "--trust-root",
        help=(
            "path to a JSON file with keyId, issuer and publicKey, obtained from the issuer "
            "independently of the bundle. Without it the signature is reported as unchecked rather "
            "than skipped silently."
        ),
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable findings")
    args = parser.parse_args(argv)

    trust_root = _load_trust_root(args.trust_root) if args.trust_root else None
    report = verify_archive(args.bundle, trust_root=trust_root)

    if args.json:
        print(
            json.dumps(
                {
                    "findings": [
                        {"check": f.check, "outcome": str(f.outcome), "detail": f.detail}
                        for f in report.findings
                    ],
                    "trustLevel": str(report.trust_level) if report.trust_level else None,
                    "attributedTo": report.attributed_to,
                    "independentlyTrusted": report.independently_trusted,
                    "integrityIntact": report.integrity_intact,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(report.human_report())

    return report.exit_code


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
