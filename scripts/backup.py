"""Take an encrypted backup of everything a restore needs, and record what it does not contain.

Four things go in, and the fourth is the one that gets forgotten:

1. **PostgreSQL**, dumped faithfully -- ownership and privileges included. Not `--no-owner`: tenant
   isolation in this system is expressed as table ownership plus `FORCE ROW LEVEL SECURITY`, so a
   dump that discarded ownership would restore into a database whose policies apply to a role that
   owns nothing, and the application role could then read no rows at all. The restore drill asserts
   this directly, because it is the mistake every `pg_dump` tutorial teaches.
2. **Evidence objects**, every key in the bucket, enumerated with pagination rather than one listing
   call that stops at a thousand.
3. **Configuration**, by name and shape. Never by value: a backup containing the object-store secret
   would put the credential and the ciphertext it protects in the same file.
4. **Key metadata** -- which signing keys exist, who issues them, and their public halves. Not the
   private halves, which live in a key management service and are not this file's to hold. A reader
   verifying a restored export needs the public key and the key id; a restore does not need the
   ability to sign.

The manifest states, in the file itself, what the backup omits and what a restore therefore cannot
re-establish. A restore procedure that discovers its gaps at restore time discovers them during an
outage.

**Known limitation: this builds the archive in memory.** Every evidence object is read into a dict
and the tar is assembled in a `BytesIO` before sealing, so peak memory is roughly the size of the
uncompressed evidence corpus. That is fine for the development corpus this was measured against
(1095 objects, a few megabytes) and it will not do for a real store. `MAX_UNSEALED_BYTES` makes the
limit a refusal with a sentence rather than an out-of-memory kill on a backup host at 3am, which is
the difference between a known limitation and an unreliable backup. Streaming the tar directly into
the envelope is the fix and it is not done here.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import shutil
import subprocess  # noqa: S404 - pg_dump is the point
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from accessforge_evidence.envelope import KEY_BYTES, generate_key, seal
from accessforge_persistence import connect, expected_migrations
from accessforge_persistence.evidence.objectstore import S3ArtifactStore, S3Settings

#: The largest archive this in-memory implementation will attempt. See the module docstring: the
#: whole tar is assembled before sealing, so exceeding this is an out-of-memory kill rather than an
#: error, and a backup host that dies silently is worse than one that refuses loudly.
MAX_UNSEALED_BYTES = 2 * 1024 * 1024 * 1024

#: What a restore cannot bring back, stated in the backup rather than discovered during one.
KNOWN_OMISSIONS = [
    "Private signing key material. It lives in a key management service; this file carries the "
    "public halves and the key ids, which is what a reader verifying an export needs.",
    "Object-store and database credentials. Names and shapes only -- a backup holding the "
    "credential for the store it was copied to is a backup that decrypts itself.",
    "Anything deleted before the snapshot. A restore does not undo a retention deletion, and "
    "module 26's retention rules apply to restored rows from the moment they are restored.",
    "The state of any physical desktop runner. A machine's availability is not in this database "
    "and cannot be restored from it.",
]


def _redacted_source(database_url: str) -> str:
    parts = urlsplit(database_url)
    host = parts.hostname or "<socket>"
    port = f":{parts.port}" if parts.port else ""
    return f"postgresql://<redacted>@{host}{port}{parts.path}"


def _dump_postgres(database_url: str, destination: Path) -> None:
    """A faithful custom-format dump, taken with a role that can see every row.

    `FORCE ROW LEVEL SECURITY` applies to the table owner, so the application role -- which owns the
    tables -- cannot `pg_dump` them: it would produce a structurally valid dump containing none of
    its own data. The backup role must be a superuser or hold BYPASSRLS, and if it does not,
    `pg_dump` fails here rather than producing an empty-looking backup that nobody notices until a
    restore.
    """
    pg_dump = shutil.which("pg_dump")
    if pg_dump is None:
        raise SystemExit(
            "pg_dump is not on PATH. Resolved absolutely rather than executed by name so that a "
            "backup cannot be taken by whatever a manipulated PATH happens to point at."
        )
    result = subprocess.run(  # noqa: S603 - argv is built here, not taken from input
        [
            pg_dump,
            "--format=custom",
            "--no-password",
            f"--file={destination}",
            database_url,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # stderr from pg_dump does not echo the password, but it does echo the rest of the URL.
        # Only the first line is kept, and the URL is not re-printed by us.
        first = (result.stderr or "").strip().splitlines()
        raise SystemExit(
            "pg_dump failed: "
            + (first[0] if first else f"exit {result.returncode}")
            + "\nA backup role must bypass row-level security; the application role owns the "
            "tables and FORCE RLS applies to owners, so it can dump structure but no rows."
        )


def _key_metadata(database_url: str) -> dict[str, Any]:
    """Which keys a restored deployment will need, from the rows that reference them."""
    with connect(database_url) as conn:
        rows = conn.execute(
            "SELECT DISTINCT signing_key_id FROM evidence_export "
            "WHERE signing_key_id IS NOT NULL ORDER BY signing_key_id"
        ).fetchall()
    return {
        "keyIdsReferencedByExports": [str(r["signing_key_id"]) for r in rows],
        "privateKeyMaterialIncluded": False,
        "detail": (
            "A restore re-establishes which key signed each export, not the ability to sign. "
            "Obtain the public half of each key id above from the issuer to verify a restored "
            "export; the private halves are in the key management service and were never here."
        ),
    }


def _configuration_shape() -> dict[str, Any]:
    """Every variable a deployment needs, by name, with values deliberately absent."""
    root = Path(__file__).resolve().parent.parent
    names = []
    # Every example file, not just the control plane's. The backup operator's own variables live in
    # a separate file because the control plane's settings forbid unknown ACCESSFORGE_ names -- so a
    # restore reading only .env.example would omit exactly the variables a restore needs.
    for example in (
        root / ".env.example",
        root / ".env.backup.example",
        root / ".env.objectstore.example",
    ):
        if not example.exists():
            continue
        for line in example.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                names.append(stripped.split("=", 1)[0])
    return {
        "requiredVariables": sorted(names),
        "valuesIncluded": False,
        "detail": (
            "Names and the example file's shape only. A restored deployment is configured from the "
            "target environment's own secret store; carrying values here would put the credential "
            "and the data it protects in one file."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Take an encrypted AccessForge backup.")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("ACCESSFORGE_BACKUP_DATABASE_URL")
        or os.environ.get("ACCESSFORGE_DATABASE_URL"),
        help=(
            "a role that bypasses row-level security; the application role owns the tables "
            "and so cannot dump its own rows"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="where to write the sealed archive. Required unless --write-new-key is the only "
        "thing being asked for.",
    )
    parser.add_argument(
        "--key-file",
        type=Path,
        default=os.environ.get("ACCESSFORGE_BACKUP_KEY_FILE"),
        help="32 raw bytes, base64-encoded. Generated with --write-new-key if absent.",
    )
    parser.add_argument(
        "--key-id", default=os.environ.get("ACCESSFORGE_BACKUP_KEY_ID", "af-backup")
    )
    parser.add_argument(
        "--write-new-key",
        action="store_true",
        help=(
            "generate a key into --key-file and, if no backup was also asked for, stop there. "
            "Refuses to overwrite: overwriting the key makes every previous backup permanently "
            "unreadable, and that is not something a flag should do quietly."
        ),
    )
    parser.add_argument(
        "--skip-evidence",
        action="store_true",
        help="database only. The manifest records the omission, so a restore from it cannot be "
        "mistaken for a complete one.",
    )
    args = parser.parse_args(argv)

    if not args.key_file:
        print("no --key-file; set ACCESSFORGE_BACKUP_KEY_FILE", file=sys.stderr)
        return 2

    # Generating a key is not taking a backup, and requiring a database URL for it was wrong.
    # CI found this: a fixture asking only for a key got exit 2 and no key, because the database
    # check ran first. The two operations are separable, so they are separated -- and creating the
    # key is the step an operator performs *before* they have anything to back up.
    key_file = Path(args.key_file)
    if args.write_new_key:
        if key_file.exists():
            print(
                f"{key_file} already exists. Overwriting it would make every backup taken with the "
                "old key permanently unreadable.",
                file=sys.stderr,
            )
            return 2
        key = generate_key()
        key_file.write_text(base64.b64encode(key).decode("ascii") + "\n", encoding="utf-8")
        key_file.chmod(0o600)
        print(
            f"wrote a new backup key to {key_file} (mode 600). "
            "Store a copy somewhere this machine is not."
        )
    else:
        if not key_file.exists():
            print(f"{key_file} does not exist; pass --write-new-key to create one", file=sys.stderr)
            return 2
        key = base64.b64decode(key_file.read_text(encoding="utf-8").strip())
        if len(key) != KEY_BYTES:
            print(f"{key_file} does not hold {KEY_BYTES} base64-encoded bytes", file=sys.stderr)
            return 2

    if args.write_new_key and args.output is None:
        return 0
    if args.output is None:
        print("no --output; pass a path for the sealed archive", file=sys.stderr)
        return 2
    if not args.database_url:
        print("no database url; set ACCESSFORGE_BACKUP_DATABASE_URL", file=sys.stderr)
        return 2

    taken_at = datetime.now(UTC)
    with tempfile.TemporaryDirectory() as staging_name:
        staging = Path(staging_name)
        dump_path = staging / "postgres.dump"
        _dump_postgres(args.database_url, dump_path)

        members: dict[str, bytes] = {}
        object_keys: list[str] = []
        if not args.skip_evidence:
            store = S3ArtifactStore(
                S3Settings(
                    endpoint_url=os.environ["ACCESSFORGE_EVIDENCE_ENDPOINT_URL"],
                    access_key=os.environ["ACCESSFORGE_EVIDENCE_ACCESS_KEY"],
                    secret_key=os.environ["ACCESSFORGE_EVIDENCE_SECRET_KEY"],
                    bucket=os.environ["ACCESSFORGE_EVIDENCE_BUCKET"],
                )
            )
            accumulated = 0
            for object_key in store.iter_keys():
                payload = store.get(key=object_key)
                accumulated += len(payload)
                if accumulated > MAX_UNSEALED_BYTES:
                    raise SystemExit(
                        f"the evidence store exceeds {MAX_UNSEALED_BYTES} bytes, which this "
                        "implementation assembles in memory before sealing. Refusing rather than "
                        "being killed part-way: an out-of-memory death during a backup leaves no "
                        "backup and no clear reason. Streaming the archive is the fix and is not "
                        "implemented; --skip-evidence takes the database alone in the meantime."
                    )
                members[f"evidence/{object_key}"] = payload
                object_keys.append(object_key)

        with connect(args.database_url) as conn:
            applied = [
                str(r["name"])
                for r in conn.execute("SELECT name FROM schema_migration ORDER BY name").fetchall()
            ]

        dump_bytes = dump_path.read_bytes()
        members["postgres.dump"] = dump_bytes
        members["configuration.json"] = json.dumps(_configuration_shape(), indent=2).encode()
        members["keys.json"] = json.dumps(_key_metadata(args.database_url), indent=2).encode()

        manifest: dict[str, Any] = {
            "format": "accessforge-backup/1",
            "takenAt": taken_at.isoformat().replace("+00:00", "Z"),
            "source": _redacted_source(args.database_url),
            "schema": {"applied": applied, "latest": applied[-1] if applied else None},
            "treeExpects": list(expected_migrations()),
            "evidenceObjects": len(object_keys),
            "evidenceIncluded": not args.skip_evidence,
            # Recorded so a restore can refuse to write these objects back into the bucket they
            # came from. Restoring into the live store overwrites current evidence with a snapshot
            # and undoes every retention deletion made since -- and without this field a restore
            # has no way to notice it is about to.
            "evidenceBucket": (
                None if args.skip_evidence else os.environ.get("ACCESSFORGE_EVIDENCE_BUCKET")
            ),
            "members": {
                name: {
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
                for name, payload in sorted(members.items())
            },
            "omits": KNOWN_OMISSIONS,
        }
        members["MANIFEST.json"] = json.dumps(manifest, indent=2, sort_keys=True).encode()

        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            for name, payload in sorted(members.items()):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mtime = int(taken_at.timestamp())
                # Fixed ownership and mode. A tar that carried the backup operator's uid would make
                # the file's contents depend on who ran it, and two backups of identical data would
                # differ for no reason anybody could explain during an incident.
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o600
                tar.addfile(info, io.BytesIO(payload))

        tar_buffer.seek(0)
        args.output.parent.mkdir(parents=True, exist_ok=True)

        # Sealed into a sibling temporary file and renamed only once it is complete and on disk.
        # `open("wb")` on the destination truncates it first, so a full disk, a sealing error or a
        # SIGKILL half-way through would replace last night's good backup with a partial one --
        # destroying the thing being protected in the act of protecting it. `os.replace` is atomic
        # within a filesystem, so an interrupted run leaves the previous backup exactly as it was
        # and a stray temporary file that this cleans up.
        staging_output = args.output.with_name(args.output.name + f".partial-{os.getpid()}")
        try:
            with staging_output.open("wb") as out:
                header = seal(tar_buffer, out, key=key, key_id=args.key_id)
                out.flush()
                os.fsync(out.fileno())
            staging_output.chmod(0o600)
            os.replace(staging_output, args.output)
        except BaseException:
            staging_output.unlink(missing_ok=True)
            raise

    size = args.output.stat().st_size
    print(f"wrote {args.output} ({size} bytes), sealed with key id {header.key_id}")
    print(f"  schema:   {applied[-1] if applied else None}")
    print(
        f"  evidence: {len(object_keys)} object(s)"
        + ("" if not args.skip_evidence else " (SKIPPED)")
    )
    print("  omits:")
    for line in KNOWN_OMISSIONS:
        print(f"    - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
