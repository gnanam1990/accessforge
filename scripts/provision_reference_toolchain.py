"""Explicit trusted E0 toolchain provisioning; no target source is sent or executed here."""

from __future__ import annotations

import argparse
from pathlib import Path

from accessforge_build_worker.sandbox import discover_daemon
from accessforge_build_worker.toolchain import provision_reference_toolchain


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    args = parser.parse_args()
    directory = Path(__file__).resolve().parents[1] / "apps" / "build-worker" / "toolchain"
    receipt = provision_reference_toolchain(directory, daemon=discover_daemon(args.endpoint))
    print(receipt.image_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
