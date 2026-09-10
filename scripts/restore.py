"""Restore an encrypted backup into an isolated target, then reconcile it.

Restoring is the easy half. The half that goes wrong is everything that was true at the snapshot and
is not true now -- live sessions, granted leases, unredeemed enrollment tokens, claimed jobs,
undelivered outbox rows, execution grants somebody revoked an hour after the backup was taken. A
system that restored and carried on would hand a desktop to a supervisor that died yesterday and
redeliver a day of events as if they were happening now.

So this script does three things in a fixed order, and refuses to skip the third:

    decrypt and verify  ->  restore into a target  ->  reconcile

`--reconcile` is on by default and turning it off prints a warning naming what is now live in the
target. There is no flag that restores into the *source* database: the target URL is compared
against the manifest's recorded source, and a match is refused. Restoring over the database you took
the backup from is not a recovery, it is the loss.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import shutil
import subprocess  # noqa: S404 - pg_restore is the point
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from accessforge_evidence.envelope import KEY_BYTES, EnvelopeError, open_sealed, read_header
from accessforge_persistence import connect, expected_migrations, migrate
from accessforge_persistence.evidence.objectstore import S3ArtifactStore, S3Settings
from accessforge_persistence.restore import reconcile, restore_is_forward_compatible

#: Members a backup may contain. Everything else is refused rather than ignored -- an archive is
#: untrusted input even when you took it yourself, because "you took it yourself" is exactly what
#: somebody handing you a doctored one would like you to believe.
_KNOWN_PREFIXES = ("evidence/",)
_KNOWN_MEMBERS = frozenset({"MANIFEST.json", "postgres.dump", "configuration.json", "keys.json"})

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024 * 1024


def _same_database(left: str, right: str) -> bool:
    """Whether two URLs name the same database on the same server.

    Compared by host, port and path rather than by string, because
    `postgresql://a:pw@localhost/db` and `postgresql://a@127.0.0.1:5432/db` are the same database
    and a string comparison would let the second restore over the first.
    """
    a, b = urlsplit(left), urlsplit(right)
    loopback = {"localhost", "127.0.0.1", "::1", None, ""}
    same_host = (a.hostname == b.hostname) or (a.hostname in loopback and b.hostname in loopback)
    return same_host and (a.port or 5432) == (b.port or 5432) and a.path == b.path


def _extract(blob: Path, key: bytes) -> dict[str, bytes]:
    """Decrypt, then read the tar entirely in memory by name.

    Nothing is written to disk during extraction and no path from the archive is ever joined to a
    filesystem path, so there is no `../../` to defend against. Names are checked against an
    allowlist and duplicates are refused: two members with one name is how a validator checks the
    first while the consumer reads the second.
    """
    plain = io.BytesIO()
    with blob.open("rb") as sealed:
        open_sealed(sealed, plain, key=key)
    if plain.tell() > MAX_ARCHIVE_BYTES:
        raise SystemExit(f"archive expands beyond {MAX_ARCHIVE_BYTES} bytes; refusing")
    plain.seek(0)

    members: dict[str, bytes] = {}
    with tarfile.open(fileobj=plain, mode="r") as tar:
        for info in tar:
            if not info.isfile():
                raise SystemExit(f"archive member {info.name!r} is not a regular file")
            if info.name in members:
                raise SystemExit(f"archive contains {info.name!r} twice")
            if info.name not in _KNOWN_MEMBERS and not info.name.startswith(_KNOWN_PREFIXES):
                raise SystemExit(f"archive contains an unexpected member {info.name!r}")
            handle = tar.extractfile(info)
            if handle is None:  # pragma: no cover - isfile() already established this
                raise SystemExit(f"cannot read archive member {info.name!r}")
            members[info.name] = handle.read()
    return members


def _verify(members: dict[str, bytes]) -> dict[str, Any]:
    """Check every member against the manifest, and the manifest against the member list.

    Both directions. Checking only the recorded digests would let an archive carry an extra member
    nobody listed; checking only the member list would let a listed member be swapped for different
    bytes of the same length. The envelope already authenticates the whole file, so this is not the
    integrity boundary -- it is the check that catches a backup written wrong rather than altered.
    """
    if "MANIFEST.json" not in members:
        raise SystemExit("no MANIFEST.json; this is not an AccessForge backup")
    manifest: dict[str, Any] = json.loads(members["MANIFEST.json"])
    recorded: dict[str, Any] = manifest["members"]

    present = set(members) - {"MANIFEST.json"}
    listed = set(recorded)
    if present != listed:
        missing = sorted(listed - present)
        extra = sorted(present - listed)
        raise SystemExit(
            f"the archive does not match its manifest. missing: {missing or 'none'}; "
            f"unlisted: {extra or 'none'}"
        )

    for name, expected in sorted(recorded.items()):
        actual = hashlib.sha256(members[name]).hexdigest()
        if actual != expected["sha256"]:
            raise SystemExit(f"member {name!r} does not match its recorded digest")
    return manifest


def _target_is_empty(target_url: str) -> bool:
    """Refuse a target that already holds tables.

    `pg_restore` into a populated database produces a wall of "already exists" errors, and with
    `--exit-on-error` it stops at the first one -- leaving a database that is neither the old
    contents nor the new. The distinction an operator needs is between "you pointed at the wrong
    database" and "the backup is broken", and one clear sentence before anything is written is the
    only place that distinction is cheap.
    """
    with connect(target_url) as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchone()
    if row and int(row["n"]) > 0:
        print(
            f"the target database already contains {row['n']} table(s). Restore into an empty "
            "database: a restore over existing tables stops part-way and leaves neither the old "
            "contents nor the new.",
            file=sys.stderr,
        )
        return False
    return True


def _restore_postgres(dump: bytes, target_url: str) -> None:
    pg_restore = shutil.which("pg_restore")
    if pg_restore is None:
        raise SystemExit("pg_restore is not on PATH")
    with tempfile.NamedTemporaryFile(suffix=".dump", delete=True) as handle:
        handle.write(dump)
        handle.flush()
        result = subprocess.run(  # noqa: S603 - argv is built here, not taken from input
            [
                pg_restore,
                "--no-password",
                "--exit-on-error",
                f"--dbname={target_url}",
                handle.name,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        first = (result.stderr or "").strip().splitlines()
        raise SystemExit(
            "pg_restore failed: " + (first[0] if first else f"exit {result.returncode}")
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument(
        "--target-database-url",
        required=True,
        help="an EMPTY database, and not the one the backup came from. Reconciliation needs a role "
        "that bypasses row-level security, and so does the restore itself.",
    )
    parser.add_argument(
        "--key-file", type=Path, default=os.environ.get("ACCESSFORGE_BACKUP_KEY_FILE")
    )
    parser.add_argument(
        "--target-bucket",
        help="an isolated bucket for the restored evidence. Deliberately has no default: it used "
        "to fall back to ACCESSFORGE_EVIDENCE_BUCKET, which points at the live store, and "
        "restoring into it resurrects objects that retention deleted after the snapshot.",
    )
    parser.add_argument(
        "--allow-restoring-into-the-source-bucket",
        action="store_true",
        help="permit --target-bucket to be the bucket the backup was taken from. This overwrites "
        "live evidence with a snapshot and undoes retention deletions; it exists so the override "
        "is explicit and appears in shell history rather than being a silent default.",
    )
    parser.add_argument(
        "--operator", default=os.environ.get("USER", "unknown"), help="recorded in the audit row"
    )
    parser.add_argument(
        "--no-reconcile",
        action="store_true",
        help="restore without invalidating restored authority. Leaves live sessions, granted "
        "leases and unredeemed enrollment tokens in the target.",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="print the manifest and stop. Answers 'what is in this file' without touching a "
        "database, which is the question asked during an incident.",
    )
    args = parser.parse_args(argv)

    if not args.key_file:
        print("no --key-file; set ACCESSFORGE_BACKUP_KEY_FILE", file=sys.stderr)
        return 2
    key = base64.b64decode(Path(args.key_file).read_text(encoding="utf-8").strip())
    if len(key) != KEY_BYTES:
        print(f"{args.key_file} does not hold {KEY_BYTES} base64-encoded bytes", file=sys.stderr)
        return 2

    with args.archive.open("rb") as handle:
        try:
            envelope = read_header(handle)
        except EnvelopeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    print(f"archive sealed with key id {envelope.key_id}")

    try:
        members = _extract(args.archive, key)
    except EnvelopeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    manifest = _verify(members)

    print(f"taken at {manifest['takenAt']} from {manifest['source']}")
    print(f"schema   {manifest['schema']['latest']}")
    print(f"evidence {manifest['evidenceObjects']} object(s)")
    print("omits:")
    for line in manifest["omits"]:
        print(f"  - {line}")

    if args.inspect:
        return 0

    if _same_database(manifest["source"], args.target_database_url):
        # The manifest's source has its credentials redacted, so this compares host, port and
        # database name -- which is the part that matters and the part a redaction preserves.
        print(
            "\nthe target is the database this backup was taken from. Restoring over it is not a "
            "recovery; restore into a disposable database and reconcile there.",
            file=sys.stderr,
        )
        return 2

    if not _target_is_empty(args.target_database_url):
        return 2
    # Evidence first, and the object store validated before PostgreSQL is touched at all.
    #
    # The order matters for what a failure leaves behind. Restoring the database first and then
    # failing on a missing bucket leaves a populated database, and the next attempt is refused by
    # `_target_is_empty` -- so a transient object-store problem turns into a manual cleanup during
    # an incident. Doing the store first means a failure there leaves the database untouched and the
    # command simply re-runnable.
    object_members = {n: p for n, p in members.items() if n.startswith("evidence/")}
    if object_members:
        if not args.target_bucket:
            print(
                f"this archive carries {len(object_members)} evidence object(s) and no "
                "--target-bucket was given. There is no default: restoring evidence into the live "
                "bucket resurrects objects that retention deleted after the snapshot.",
                file=sys.stderr,
            )
            return 2
        source_bucket = manifest.get("evidenceBucket")
        if (
            source_bucket
            and args.target_bucket == source_bucket
            and not args.allow_restoring_into_the_source_bucket
        ):
            print(
                f"--target-bucket is {args.target_bucket!r}, the bucket this backup was taken "
                "from. Restoring into it overwrites live evidence with a snapshot and undoes every "
                "retention deletion made since. Pass "
                "--allow-restoring-into-the-source-bucket if that is genuinely what you want.",
                file=sys.stderr,
            )
            return 2
        store = S3ArtifactStore(
            S3Settings(
                endpoint_url=os.environ["ACCESSFORGE_EVIDENCE_ENDPOINT_URL"],
                access_key=os.environ["ACCESSFORGE_EVIDENCE_ACCESS_KEY"],
                secret_key=os.environ["ACCESSFORGE_EVIDENCE_SECRET_KEY"],
                bucket=args.target_bucket,
            )
        )
        store.ensure_bucket()
        for name, payload in sorted(object_members.items()):
            store.put(
                key=name.removeprefix("evidence/"),
                payload=payload,
                content_type="application/octet-stream",
            )
        print(f"restored {len(object_members)} evidence object(s) into {args.target_bucket}")

    _restore_postgres(members["postgres.dump"], args.target_database_url)
    print(f"restored PostgreSQL into {urlsplit(args.target_database_url).path.lstrip('/')}")

    # The restored schema is checked and brought forward before anything reconciles against it.
    # Without this, a backup taken before migration 0014 restores cleanly and then fails inside
    # reconciliation on a CHECK constraint that does not yet allow 'RESTORED_DATABASE' -- after the
    # database and every evidence object have already landed. A schema *ahead* of this build is
    # refused outright: old code reads unknown columns as absent, which is indistinguishable from a
    # column being empty.
    with connect(args.target_database_url) as conn:
        compatible, detail = restore_is_forward_compatible(conn, expected=expected_migrations())
    print(f"schema: {detail}")
    if not compatible:
        print("\n" + detail, file=sys.stderr)
        return 2
    applied = migrate(args.target_database_url)
    if applied:
        print(f"migrated the restored database forward: {', '.join(applied)}")

    if args.no_reconcile:
        print(
            "\nNOT RECONCILED. This database currently contains live sessions, granted desktop "
            "leases, unredeemed enrollment tokens, claimed jobs and undelivered outbox rows that "
            "were true at the snapshot and are not true now. Do not point a running deployment at "
            "it. Reconcile with:\n"
            "  uv run python scripts/restore.py --archive ... --target-database-url ... "
            "(without --no-reconcile)"
        )
        return 0

    with connect(args.target_database_url) as conn:
        # The archive's own stream id identifies this restore. Scoping the once-only guarantee to
        # the restore rather than to the database is what stops the audit row -- which travels in
        # every backup taken afterwards -- from blocking the next real restore years later.
        report = reconcile(conn, operator=args.operator, restore_id=envelope.stream_id)
        conn.commit()
    print("\nreconciled: " + report.summary)
    if report.grants_requiring_revalidation:
        print(
            "\nEvery execution grant below must be revalidated by a person before anything is "
            "dispatched under it. A grant revoked after the snapshot is live in this data and "
            "revoked in the world, and nothing here can tell the difference:"
        )
        for grant in report.grants_requiring_revalidation:
            print(f"  - {grant}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
