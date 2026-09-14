"""Synthetic object-store failures verify restore routing, not an actual S3 restore."""

import uuid
from typing import cast

import pytest

from accessforge_build_worker.baseline_artifacts import archive_key
from accessforge_persistence.evidence.objectstore import (
    ObjectStoreUnavailable,
    S3ArtifactStore,
    is_baseline_archive_key,
    is_candidate_archive_key,
)
from accessforge_persistence.restore import restore_object_bytes


@pytest.mark.parametrize("existing", [None, b"", b"original", b"different"])
def test_baseline_restore_never_unconditionally_overwrites_a_key(existing: bytes | None) -> None:
    key = archive_key(str(uuid.uuid4()), str(uuid.uuid4()), "a" * 64)
    assert is_baseline_archive_key(key) and not is_candidate_archive_key(key)
    assert not is_baseline_archive_key(key + "/extra")

    class Store:
        def __init__(self) -> None:
            self.payload = existing

        def put_create_only(self, *, key: str, payload: bytes, content_type: str) -> str:
            if self.payload is not None:
                raise ObjectStoreUnavailable("synthetic create-only conflict")
            self.payload = payload
            return key

        def put(self, **kwargs: object) -> None:
            pytest.fail("baseline restore must not use overwrite")

        def get_bounded(self, *, key: str, max_bytes: int) -> bytes:
            assert self.payload is not None
            return self.payload[:max_bytes]

    store = Store()
    if existing in (b"", b"different"):
        with pytest.raises(ObjectStoreUnavailable):
            restore_object_bytes(cast(S3ArtifactStore, store), key=key, payload=b"original")
        assert store.payload == existing
    else:
        restore_object_bytes(cast(S3ArtifactStore, store), key=key, payload=b"original")
        assert store.payload == b"original"
    if existing == b"":
        restore_object_bytes(cast(S3ArtifactStore, store), key=key, payload=b"")
        assert store.payload == b""
