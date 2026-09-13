"""Check built archives for exact schemas, migrations and runtime dependencies."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_core_packages(directory: Path) -> None:
    schemas = {
        p.name: p.read_bytes() for p in (ROOT / "packages/contracts/schemas").glob("*.schema.json")
    }
    migrations = {
        p.name: p.read_bytes()
        for p in (ROOT / "packages/persistence/src/accessforge_persistence/migrations").glob(
            "*.sql"
        )
    }
    if not schemas or not migrations:
        raise ValueError("authoritative package resources are missing")
    for folder in (directory, directory / "direct"):
        with zipfile.ZipFile(folder / "accessforge_contracts-0.0.0-py3-none-any.whl") as archive:
            _check_wheel(archive, "accessforge_contracts/schemas/", schemas)
    with zipfile.ZipFile(directory / "accessforge_persistence-0.0.0-py3-none-any.whl") as archive:
        _check_wheel(archive, "accessforge_persistence/migrations/", migrations)
        metadata = archive.read("accessforge_persistence-0.0.0.dist-info/METADATA").decode()
        if "Requires-Dist: accessforge-contracts" not in metadata.splitlines():
            raise ValueError("persistence wheel omits its runtime contract dependency")
    with tarfile.open(directory / "accessforge_contracts-0.0.0.tar.gz", "r:gz") as archive:
        observed = {}
        for member in archive.getmembers():
            prefix = "accessforge_contracts-0.0.0/src/accessforge_contracts/schemas/"
            if member.name.startswith(prefix) and member.isfile():
                stream = archive.extractfile(member)
                assert stream is not None
                name = member.name.removeprefix(prefix)
                if name in observed:
                    raise ValueError("duplicate source-distribution schema")
                observed[name] = stream.read()
        if observed != schemas:
            raise ValueError("source distribution does not carry exact authoritative schemas")
    print(
        f"Core artifacts verified: {len(schemas)} exact schemas, {len(migrations)} exact migrations"
    )


def _check_wheel(archive: zipfile.ZipFile, prefix: str, expected: dict[str, bytes]) -> None:
    if len(archive.namelist()) != len(set(archive.namelist())):
        raise ValueError("wheel has duplicate archive paths")
    observed = {
        name.removeprefix(prefix): archive.read(name)
        for name in archive.namelist()
        if name.startswith(prefix) and not name.endswith("/")
    }
    if observed != expected:
        raise ValueError("wheel resources differ from authoritative source: " + prefix)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    check_core_packages(parser.parse_args().directory)
