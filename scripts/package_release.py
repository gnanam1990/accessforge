"""Package committed source and freshly built web assets; never deploy or qualify a reader."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 128 * 1024 * 1024


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(  # noqa: S603 - fixed git operations, no shell
        ["git", "-C", str(root), *args],  # noqa: S607 - operator-provisioned git
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout


def package(root: Path, web: Path, output: Path) -> str:
    """Refuse dirty tracked inputs, symlinks and overwrite; omit all untracked source files.

    Web bytes must be freshly built by the caller. The manifest is a content inventory, not
    an attestation that supplied assets were built from the commit or that CI/acceptance passed.
    The release workflow performs the build and packaging together after required CI.
    """
    revision = git(root, "rev-parse", "HEAD").decode().strip()
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("release requires a clean tracked checkout")
    if output.exists() or output.is_symlink():
        raise ValueError("release output already exists")
    if web.is_symlink() or not web.is_dir() or not (web / "index.html").is_file():
        raise ValueError("built web index is required")
    files: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    total = 0

    def add(name: str, data: bytes, executable: bool = False) -> None:
        nonlocal total
        parts = PurePosixPath(name)
        if parts.is_absolute() or ".." in parts.parts or name in files:
            raise ValueError("unsafe or duplicate release member")
        total += len(data)
        if total > MAX_BYTES or len(files) >= 10000:
            raise ValueError("release exceeds packaging limits")
        files[name] = data
        modes[name] = 0o755 if executable else 0o644

    archive = git(root, "archive", "--format=tar", revision)
    if len(archive) > MAX_BYTES:
        raise ValueError("source archive exceeds packaging limit")
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for member in source:
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("source symlinks and special files are not release inputs")
            stream = source.extractfile(member)
            if stream is None or member.size > MAX_BYTES:
                raise ValueError("source member unavailable")
            add(f"source/{member.name}", stream.read(), bool(member.mode & 0o111))
    for path in sorted(web.rglob("*")):
        if path.is_symlink():
            raise ValueError("web symlinks are not release inputs")
        if path.is_dir():
            continue
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise ValueError("web member unavailable")
        add(f"web/{path.relative_to(web).as_posix()}", path.read_bytes())
    for required in ("pyproject.toml", "uv.lock", "pnpm-lock.yaml", "infra/version-matrix.toml"):
        if f"source/{required}" not in files:
            raise ValueError("required source install contract is missing")
    add(
        "README.txt",
        (
            "AccessForge source + web release candidate\n"
            f"Source commit: {revision}\n\n"
            "Install from source/ using uv sync --frozen and the operator documentation.\n"
            "See source/docs/operations/DEPLOYMENT.md for configuration and explicit migrations.\n"
            "Serve web/ static assets with SPA fallback and same-origin /v1 API routing.\n"
            "Set ACCESSFORGE_WEB_DIST_DIRECTORY to the absolute web/ path for API UI serving.\n"
            "Do not use the Vite development server as production hosting.\n\n"
            "No credentials, dependencies, database, reader binaries or Codex login are bundled.\n"
            "AWS/Bedrock is retired. Codex OAuth is model integration, not a hosting provider.\n"
            "Actual VoiceOver/NVDA, model execution, full repair/rerun/review and deployment\n"
            "acceptance are not established by this bundle. Consult retained runtime evidence.\n"
            "The manifest hashes inventory bytes; they are not a signature or a CI attestation.\n"
        ).encode(),
    )
    manifest = {
        "schemaVersion": 1,
        "sourceCommit": revision,
        "kind": "SOURCE_AND_WEB_RELEASE_CANDIDATE",
        "actualReaderQualified": False,
        "deploymentVerified": False,
        "files": [
            {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in sorted(files.items())
        ],
    }
    add("manifest.json", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    if git(root, "rev-parse", "HEAD").decode().strip() != revision or git(
        root, "status", "--porcelain", "--untracked-files=no"
    ):
        raise ValueError("source changed during packaging")
    # Fixed member timestamps/order make equal source and asset bytes reproduce the same ZIP.
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100000 | modes[name]) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, data)
    return hashlib.sha256(output.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-dist", type=Path, default=ROOT / "apps/web/dist")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    checksum = package(ROOT, args.web_dist, args.output)
    print(f"{checksum}  {args.output.name}")


if __name__ == "__main__":
    main()
