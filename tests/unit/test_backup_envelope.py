"""The backup envelope, tested by performing each attack its docstring claims to stop.

A test that only round-trips proves the happy path and nothing else, and the happy path is not why
this format has framing. Every test below takes a valid sealed archive and damages it in one
specific way that a naive chunked scheme would accept.
"""

from __future__ import annotations

import io
import json
import struct

import pytest

from accessforge_evidence.envelope import (
    CHUNK_BYTES,
    MAGIC,
    EnvelopeError,
    generate_key,
    open_sealed,
    read_header,
    seal,
)


def _sealed(payload: bytes, key: bytes, *, key_id: str = "af-backup-2026-09") -> bytes:
    out = io.BytesIO()
    seal(io.BytesIO(payload), out, key=key, key_id=key_id)
    return out.getvalue()


def _opened(blob: bytes, key: bytes) -> bytes:
    out = io.BytesIO()
    open_sealed(io.BytesIO(blob), out, key=key)
    return out.getvalue()


def _chunk_offsets(blob: bytes) -> list[tuple[int, int]]:
    """Where each sealed chunk starts and how long it is, so a test can cut precisely."""
    (header_length,) = struct.unpack(">I", blob[len(MAGIC) : len(MAGIC) + 4])
    cursor = len(MAGIC) + 4 + header_length
    spans = []
    while cursor < len(blob):
        (length,) = struct.unpack(">I", blob[cursor : cursor + 4])
        spans.append((cursor, 4 + length))
        cursor += 4 + length
    return spans


def test_a_sealed_archive_round_trips() -> None:
    key = generate_key()
    payload = b"the quick brown fox" * 1000
    assert _opened(_sealed(payload, key), key) == payload


def test_an_empty_archive_is_distinguishable_from_a_destroyed_one() -> None:
    """Zero bytes of content still produces one chunk, marked final.

    If an empty payload wrote no chunks at all, a file truncated to its header would decrypt to
    empty and report success -- and "the backup was empty" and "the backup was destroyed" would be
    the same observation.
    """
    key = generate_key()
    blob = _sealed(b"", key)
    assert _opened(blob, key) == b""

    (header_length,) = struct.unpack(">I", blob[len(MAGIC) : len(MAGIC) + 4])
    header_only = blob[: len(MAGIC) + 4 + header_length]
    with pytest.raises(EnvelopeError, match="without one marked final"):
        _opened(header_only, key)


def test_several_chunks_round_trip() -> None:
    key = generate_key()
    payload = bytes(range(256)) * (CHUNK_BYTES // 128)
    assert len(_chunk_offsets(_sealed(payload, key))) > 1
    assert _opened(_sealed(payload, key), key) == payload


def test_truncating_the_last_chunk_is_refused_rather_than_shortening_the_restore() -> None:
    key = generate_key()
    payload = b"x" * (CHUNK_BYTES * 3)
    blob = _sealed(payload, key)
    spans = _chunk_offsets(blob)
    assert len(spans) >= 3
    start, _ = spans[-1]

    with pytest.raises(EnvelopeError, match="without one marked final"):
        _opened(blob[:start], key)


def test_reordering_two_chunks_is_refused() -> None:
    key = generate_key()
    payload = bytes([1]) * CHUNK_BYTES + bytes([2]) * CHUNK_BYTES + bytes([3]) * 10
    blob = _sealed(payload, key)
    spans = _chunk_offsets(blob)
    assert len(spans) == 3

    def piece(i: int) -> bytes:
        start, length = spans[i]
        return blob[start : start + length]

    swapped = blob[: spans[0][0]] + piece(1) + piece(0) + piece(2)
    with pytest.raises(EnvelopeError, match="does not authenticate"):
        _opened(swapped, key)


def test_a_chunk_spliced_in_from_another_backup_is_refused() -> None:
    """Same key, same position, different archive.

    Without the per-archive `stream_id` in the header -- and the header in the AAD -- this splice
    would authenticate, because the key, the nonce and the chunk index all match.
    """
    key = generate_key()
    first = _sealed(b"a" * CHUNK_BYTES + b"tail-one", key)
    second = _sealed(b"b" * CHUNK_BYTES + b"tail-two", key)

    spans_first = _chunk_offsets(first)
    spans_second = _chunk_offsets(second)
    start_f, length_f = spans_first[0]
    start_s, length_s = spans_second[0]
    assert length_f == length_s

    spliced = first[:start_f] + second[start_s : start_s + length_s] + first[start_f + length_f :]
    with pytest.raises(EnvelopeError, match="does not authenticate"):
        _opened(spliced, key)


def test_flipping_a_byte_of_ciphertext_is_refused() -> None:
    key = generate_key()
    blob = bytearray(_sealed(b"payload" * 500, key))
    start, _ = _chunk_offsets(bytes(blob))[0]
    blob[start + 8] ^= 0x01
    with pytest.raises(EnvelopeError, match="does not authenticate"):
        _opened(bytes(blob), key)


def test_the_wrong_key_is_refused_rather_than_producing_garbage() -> None:
    blob = _sealed(b"payload", generate_key())
    with pytest.raises(EnvelopeError, match="does not authenticate"):
        _opened(blob, generate_key())


def test_a_file_that_is_not_a_backup_is_refused_by_its_marker() -> None:
    with pytest.raises(EnvelopeError, match="not an AccessForge backup"):
        _opened(b"PK\x03\x04 this is a zip file", generate_key())


def test_the_header_names_the_key_without_the_key() -> None:
    """An operator holding only the file must be able to learn which key to fetch.

    "Try every key you have" is not a recovery procedure, and a format that forced it would make
    key rotation a reason not to rotate.
    """
    blob = _sealed(b"payload", generate_key(), key_id="af-backup-2026-09")
    header = read_header(io.BytesIO(blob))
    assert header.key_id == "af-backup-2026-09"


def test_the_header_contains_no_key_material() -> None:
    key = generate_key()
    blob = _sealed(b"payload", key, key_id="af-backup-2026-09")
    assert key not in blob
    header = read_header(io.BytesIO(blob))
    rendered = header.as_bytes()
    assert key.hex().encode() not in rendered
    assert key not in rendered


def test_a_key_of_the_wrong_length_is_refused_before_anything_is_written() -> None:
    out = io.BytesIO()
    with pytest.raises(EnvelopeError, match="key must be 32 bytes"):
        seal(io.BytesIO(b"payload"), out, key=b"short", key_id="k")
    assert out.getvalue() == b""


def test_two_archives_do_not_reuse_a_nonce_prefix_by_construction() -> None:
    """Not a proof -- a randomness check cannot be one -- but it catches a hard-coded prefix.

    The property that matters is that the prefix is recorded in the header, so nonce reuse across
    archives is something an auditor can detect rather than something they must assume did not
    happen.
    """
    key = generate_key()
    prefixes = {read_header(io.BytesIO(_sealed(b"x", key))).nonce_prefix for _ in range(16)}
    assert len(prefixes) > 1


def test_a_hostile_header_cannot_choose_the_chunk_size_bound() -> None:
    """The header is plaintext and read before any tag is checked.

    The per-chunk length check compares against `chunkBytes` *from the header*, so without an
    independent bound the attacker picks the threshold: a header declaring a 4 GiB chunk size makes
    "reject an implausible chunk length" accept a 4 GiB allocation.
    """
    key = generate_key()
    blob = bytearray(_sealed(b"payload", key))
    (header_length,) = struct.unpack(">I", blob[len(MAGIC) : len(MAGIC) + 4])
    start = len(MAGIC) + 4
    header = json.loads(bytes(blob[start : start + header_length]))
    header["chunkBytes"] = 4 * 1024 * 1024 * 1024
    replacement = json.dumps(header, sort_keys=True).encode()
    forged = (
        bytes(blob[: len(MAGIC)])
        + struct.pack(">I", len(replacement))
        + replacement
        + bytes(blob[start + header_length :])
    )

    with pytest.raises(EnvelopeError, match="chunk size"):
        _opened(forged, key)


def test_a_header_declaring_the_wrong_nonce_width_is_refused() -> None:
    """A v1-shaped header reaching v2 code would derive the wrong nonce for every chunk and be
    reported as tampered -- a true statement about the wrong question. This says the real one."""
    key = generate_key()
    blob = bytearray(_sealed(b"payload", key))
    (header_length,) = struct.unpack(">I", blob[len(MAGIC) : len(MAGIC) + 4])
    start = len(MAGIC) + 4
    header = json.loads(bytes(blob[start : start + header_length]))
    header["noncePrefix"] = header["noncePrefix"][:8]  # four bytes, as version 1 wrote
    replacement = json.dumps(header, sort_keys=True).encode()
    forged = (
        bytes(blob[: len(MAGIC)])
        + struct.pack(">I", len(replacement))
        + replacement
        + bytes(blob[start + header_length :])
    )

    with pytest.raises(EnvelopeError, match="nonce prefix"):
        _opened(forged, key)


def test_the_nonce_prefix_is_wide_enough_to_survive_years_of_backups() -> None:
    """Eight bytes, not four.

    Four collides with probability 1/2 after roughly 65,000 archives under one key. Hourly backups
    across a handful of deployments sharing a key reach that inside a decade, and the consequence of
    a repeat is not a detectable error -- it is GCM nonce reuse, which breaks confidentiality and
    permits forgery before any tag is checked.
    """
    from accessforge_evidence.envelope import NONCE_PREFIX_BYTES

    assert NONCE_PREFIX_BYTES >= 8
    header = read_header(io.BytesIO(_sealed(b"x", generate_key())))
    assert len(bytes.fromhex(header.nonce_prefix)) == NONCE_PREFIX_BYTES
