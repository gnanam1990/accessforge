"""Trusted frontend baked into the image; repository backend runs only inside its sandbox."""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    # The source's own PEP 517 backend is allowed to execute, but cannot fetch dependencies.
    # No tests, credentials, user checkout, client configuration or host interpreter enter here.
    result = subprocess.run(  # noqa: S603 - fixed argv; source executes only inside the sandbox.
        [
            sys.executable,
            "-I",
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--no-index",
            "--no-cache-dir",
            "--wheel-dir=/work/out",
            "/work/src",
        ],
        env={
            "PATH": os.defpath,
            "HOME": "/work",
            "TMPDIR": "/work/tmp",
            "PIP_CONFIG_FILE": "/dev/null",
            "PYTHONDONTWRITEBYTECODE": "1",
            "SOURCE_DATE_EPOCH": "946684800",
        },
        check=False,
        timeout=120,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
