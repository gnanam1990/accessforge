"""The artifact object store.

An S3-compatible store, reached through boto3, with the bucket and prefix discipline that makes
tenant isolation mean something outside PostgreSQL. Row-level security protects rows; it protects
nothing in a bucket, and an evidence system whose files are all readable with one credential has
tenant isolation in the database and none where the transcripts actually live (INV-07).

Three decisions are load-bearing.

**Keys are constructed, never accepted.** :func:`artifact_key` builds the key from the workspace,
run, attempt and digest. A caller cannot supply a key, so a caller cannot supply
``../../other-tenant/transcript.txt`` or an absolute path or a name with a newline in it. Validating
a caller-supplied key would mean enumerating every way a path can escape a prefix, which is the
losing side of that problem.

**The digest is computed here from the bytes received.** Never taken from the caller. An uploader
able to declare the hash of its own upload can declare the hash of bytes it did not send, and every
later integrity check would confirm the declaration rather than the content.

**Nothing is promoted on arrival.** An upload lands in quarantine. Size, content type and manifest
binding are checked against what the server measured, and only then does the object become evidence.
The module prompt puts it plainly: no artifact supplied by an agent becomes trusted because its JSON
says PASS.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

#: Ceiling on a single artifact. A speech transcript for a bounded journey is kilobytes; a
#: hundred megabytes is a bug or an attempt to exhaust storage, and either way it is not evidence.
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024

#: Content types an artifact may declare, mapped to the kinds that may carry them.
#:
#: An allowlist, because the interesting failure is an HTML or SVG "transcript" that executes when
#: someone opens it in a browser from an export. Evidence is read by people, and a stored file that
#: runs script when viewed is a stored XSS with a chain of custody.
ALLOWED_CONTENT_TYPES: dict[str, frozenset[str]] = {
    "SPEECH_TRANSCRIPT": frozenset({"application/json", "text/plain"}),
    "ACTION_TRACE": frozenset({"application/json"}),
    "RUNNER_JOURNAL": frozenset({"application/x-ndjson", "application/json"}),
    "PREFLIGHT_RECORD": frozenset({"application/json"}),
    "EFFECT_RECEIPT": frozenset({"application/json"}),
    "DIAGNOSTIC_LOG": frozenset({"text/plain", "application/json"}),
    "SCREENSHOT": frozenset({"image/png"}),
}

_KEY_SEGMENT = re.compile(r"\A[0-9a-fA-F-]{36}\Z")


class ArtifactStoreError(Exception):
    """An object-store operation was refused or failed."""


class ObjectStoreUnavailable(ArtifactStoreError):
    """The store could not be reached.

    A distinct type because the correct response differs. An unavailable store blocks finalization —
    a run whose required artifacts cannot be stored is not complete — and the module prompt is
    explicit that it must never be replaced with an empty success object. Conflating it with a
    validation failure would let "the bucket is down" read as "this artifact was rejected".
    """


def artifact_key(
    *, workspace_id: str, run_id: str, attempt_id: str, kind: str, content_digest: str
) -> str:
    """Build the object key. Every component is validated; none is taken as given.

    The digest is part of the key, which makes the key content-addressed: re-uploading identical
    bytes writes the same object, and different bytes cannot overwrite an existing artifact by
    reusing its name.
    """
    for name, value in (
        ("workspace_id", workspace_id),
        ("run_id", run_id),
        ("attempt_id", attempt_id),
    ):
        if not _KEY_SEGMENT.match(value):
            raise ArtifactStoreError(
                f"{name} {value!r} is not a uuid; object keys are built from validated identifiers "
                "rather than from caller-supplied strings, so that no caller can shape a key"
            )
    if kind not in ALLOWED_CONTENT_TYPES:
        raise ArtifactStoreError(f"{kind!r} is not a known artifact kind")
    if not re.fullmatch(r"[0-9a-f]{64}", content_digest):
        raise ArtifactStoreError("content digest must be a lowercase sha-256 hex string")
    return f"workspaces/{workspace_id}/runs/{run_id}/attempts/{attempt_id}/{kind}/{content_digest}"


def compute_digest(payload: bytes) -> str:
    """The server's own hash of the bytes it received."""
    return hashlib.sha256(payload).hexdigest()


def assert_uploadable(*, kind: str, content_type: str, payload: bytes) -> None:
    """Refuse anything that must not be stored, before it is stored.

    Order matters slightly: size first, because it is the check that protects the process itself,
    and an enormous payload should be refused before anything else looks at it.
    """
    if len(payload) == 0:
        raise ArtifactStoreError(
            "an empty artifact is not evidence. A producer with nothing to say closes its stream "
            "with a watermark; it does not upload zero bytes."
        )
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise ArtifactStoreError(
            f"{len(payload)} bytes exceeds the {MAX_ARTIFACT_BYTES}-byte limit for one artifact"
        )
    permitted = ALLOWED_CONTENT_TYPES.get(kind)
    if permitted is None:
        raise ArtifactStoreError(f"{kind!r} is not a known artifact kind")
    if content_type not in permitted:
        raise ArtifactStoreError(
            f"content type {content_type!r} is not permitted for {kind}; the permitted types are "
            f"{', '.join(sorted(permitted))}. The allowlist exists because a stored HTML or SVG "
            "'transcript' executes when someone opens it from an export, which is a stored "
            "cross-site scripting payload with a chain of custody."
        )


class ArtifactStore(Protocol):
    """What the ingestion service needs from an object store."""

    def put(self, *, key: str, payload: bytes, content_type: str) -> str: ...

    def get(self, *, key: str) -> bytes: ...

    def delete(self, *, key: str) -> None: ...

    def exists(self, *, key: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class S3Settings:
    endpoint_url: str
    access_key: str
    secret_key: str
    bucket: str
    region: str = "us-east-1"


class S3ArtifactStore:
    """A real S3-compatible store. Exercised in tests against MinIO, not a fake.

    The distinction matters more than it looks. A fake would pass every test about size limits and
    content types while proving nothing about the things that only a real store does: server-side
    ETag computation, bucket-scoped credentials, and the specific errors a missing key produces.
    """

    def __init__(self, settings: S3Settings) -> None:
        import boto3
        from botocore.config import Config

        self._bucket = settings.bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.endpoint_url,
            aws_access_key_id=settings.access_key,
            aws_secret_access_key=settings.secret_key,
            region_name=settings.region,
            # Path style, because a MinIO endpoint reached by IP cannot serve virtual-host style
            # buckets, and the retry count is low on purpose: a store that is down should block
            # finalization quickly and visibly rather than after a long silence.
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 2}),
        )

    def ensure_bucket(self) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            try:
                self._client.create_bucket(Bucket=self._bucket)
            except (BotoCoreError, ClientError) as exc:
                raise ObjectStoreUnavailable(f"cannot create bucket {self._bucket}: {exc}") from exc
        except BotoCoreError as exc:
            raise ObjectStoreUnavailable(f"cannot reach the object store: {exc}") from exc

    def put(self, *, key: str, payload: bytes, content_type: str) -> str:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=payload,
                ContentType=content_type,
                # Told to the store so that a browser fetching this object from an export is told
                # to download it rather than render it. Defence in depth behind the content-type
                # allowlist, not instead of it.
                ContentDisposition="attachment",
            )
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStoreUnavailable(f"upload of {key} failed: {exc}") from exc
        return key

    def get(self, *, key: str) -> bytes:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            response: Any = self._client.get_object(Bucket=self._bucket, Key=key)
            return bytes(response["Body"].read())
        except ClientError as exc:
            raise ArtifactStoreError(f"no object at {key}: {exc}") from exc
        except BotoCoreError as exc:
            raise ObjectStoreUnavailable(f"cannot reach the object store: {exc}") from exc

    def delete(self, *, key: str) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStoreUnavailable(f"delete of {key} failed: {exc}") from exc

    def exists(self, *, key: str) -> bool:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError:
            return False
        except BotoCoreError as exc:
            raise ObjectStoreUnavailable(f"cannot reach the object store: {exc}") from exc

    def iter_keys(self, *, prefix: str = "") -> Iterator[str]:
        """Every object key in the bucket, paginated.

        For backup, which is the one operation that must enumerate the store rather than address a
        known key. Paginated rather than a single `list_objects_v2` call, because that call returns
        at most 1000 keys and silently stops there -- a backup that copied the first thousand
        artifacts and reported success is precisely the failure this product exists to argue
        against.
        """
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for item in page.get("Contents", []):
                    yield str(item["Key"])
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStoreUnavailable(f"cannot list {self._bucket}: {exc}") from exc
