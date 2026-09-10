"""Authenticated encryption for backup archives, framed so a truncation is an error.

A backup holds every tenant's evidence, so it is the single densest concentration of other people's
data this system produces. It is also the artifact most likely to be copied to a laptop, an object
store in another account, or a USB disk somebody carries home. Encryption at rest here is not
defence in depth; it is the only control that follows the file.

AES-256-GCM, and the framing is where the care goes. Three attacks that a naive "encrypt each chunk"
scheme permits, and what stops each:

**Truncation.** Drop the last N chunks and the plaintext decrypts cleanly as a shorter backup. A
restore from it succeeds and silently omits the most recent data. Every chunk therefore carries its
own index *and* a final-chunk flag in the additional authenticated data, and the reader refuses a
stream that ends without a chunk marked final.

**Reordering and splicing.** Swap two chunks, or graft chunks from a different backup taken with the
same key. Binding the index into the AAD stops the swap; binding a per-archive random `stream_id`
stops the splice, because a chunk from another archive authenticates under a different AAD.

**Nonce reuse.** Reusing a (key, nonce) pair under GCM is catastrophic -- it leaks the XOR of two
plaintexts and, worse, allows forgery. The nonce is a random 4-byte stream prefix followed by an
8-byte counter, so two chunks of one archive can never collide, and two archives collide only if the
random prefix repeats, which the header records so it is checkable rather than assumed.

The key never appears in this module's output, in an exception message, or in the header. What the
header does record is the key *identifier*, because a restore two years from now needs to know which
key to fetch, and "try them all" is not a recovery procedure.
"""

from __future__ import annotations

import json
import os
import struct
from dataclasses import dataclass
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"AFBK1\n"
"""Format marker and version. A reader that finds anything else refuses rather than guessing:
a backup file is exactly the kind of thing that gets renamed, and a decryptor that tried its best
on an unrecognised file would produce plausible garbage."""

KEY_BYTES = 32
CHUNK_BYTES = 1024 * 1024
NONCE_PREFIX_BYTES = 4
TAG_BYTES = 16

#: A hard ceiling on the number of chunks, so a corrupt length prefix cannot make a reader loop
#: forever. One million chunks is a terabyte, well beyond anything this format is meant to carry.
MAX_CHUNKS = 1_000_000


class EnvelopeError(Exception):
    """The archive could not be read, and the reason is never "probably fine"."""


@dataclass(frozen=True, slots=True)
class EnvelopeHeader:
    """What a reader needs before it holds the key, and nothing that helps without it."""

    key_id: str
    stream_id: str
    nonce_prefix: str
    chunk_bytes: int

    def as_bytes(self) -> bytes:
        return json.dumps(
            {
                "keyId": self.key_id,
                "streamId": self.stream_id,
                "noncePrefix": self.nonce_prefix,
                "chunkBytes": self.chunk_bytes,
            },
            sort_keys=True,
        ).encode("utf-8")


def generate_key() -> bytes:
    return os.urandom(KEY_BYTES)


def _aad(header: bytes, index: int, *, final: bool) -> bytes:
    """What each chunk's tag commits to besides its own bytes.

    The header is included in full, so a reader cannot be fed one archive's header with another's
    chunks. The index and the final flag are what make truncation and reordering detectable.
    """
    return header + struct.pack(">QB", index, 1 if final else 0)


def seal(source: BinaryIO, destination: BinaryIO, *, key: bytes, key_id: str) -> EnvelopeHeader:
    """Encrypt `source` into `destination`, streaming.

    Streaming rather than reading the whole thing into memory, because a backup of a real evidence
    store is larger than the machine taking it. That constraint is what forces the chunked framing,
    and the framing is what makes the integrity properties above achievable at all -- a single
    one-shot GCM message would give them for free but would not fit.
    """
    if len(key) != KEY_BYTES:
        raise EnvelopeError(f"key must be {KEY_BYTES} bytes; got {len(key)}")

    prefix = os.urandom(NONCE_PREFIX_BYTES)
    header = EnvelopeHeader(
        key_id=key_id,
        stream_id=os.urandom(16).hex(),
        nonce_prefix=prefix.hex(),
        chunk_bytes=CHUNK_BYTES,
    )
    header_bytes = header.as_bytes()

    destination.write(MAGIC)
    destination.write(struct.pack(">I", len(header_bytes)))
    destination.write(header_bytes)

    aesgcm = AESGCM(key)
    index = 0
    pending = source.read(CHUNK_BYTES)
    while True:
        lookahead = source.read(CHUNK_BYTES)
        final = not lookahead
        # An empty source still writes one chunk, marked final. Zero chunks would be
        # indistinguishable from a file truncated to its header, and "empty backup" and "destroyed
        # backup" must not look alike.
        nonce = prefix + struct.pack(">Q", index)
        sealed = aesgcm.encrypt(nonce, pending, _aad(header_bytes, index, final=final))
        destination.write(struct.pack(">I", len(sealed)))
        destination.write(sealed)
        if final:
            break
        pending = lookahead
        index += 1
        if index > MAX_CHUNKS:
            raise EnvelopeError(f"archive exceeds {MAX_CHUNKS} chunks")

    return header


def read_header(source: BinaryIO) -> EnvelopeHeader:
    """Read the header without the key, so an operator can ask which key a file needs."""
    if source.read(len(MAGIC)) != MAGIC:
        raise EnvelopeError(
            "this file does not begin with the AccessForge backup marker. It is not an "
            "AccessForge backup, or it has been truncated at the front."
        )
    raw_length = source.read(4)
    if len(raw_length) != 4:
        raise EnvelopeError("truncated before the header length")
    (length,) = struct.unpack(">I", raw_length)
    if length > 64 * 1024:
        raise EnvelopeError("header length is implausible; refusing to allocate for it")
    header_bytes = source.read(length)
    if len(header_bytes) != length:
        raise EnvelopeError("truncated inside the header")
    try:
        parsed = json.loads(header_bytes)
        return EnvelopeHeader(
            key_id=str(parsed["keyId"]),
            stream_id=str(parsed["streamId"]),
            nonce_prefix=str(parsed["noncePrefix"]),
            chunk_bytes=int(parsed["chunkBytes"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise EnvelopeError("the header is not a readable AccessForge backup header") from exc


def open_sealed(source: BinaryIO, destination: BinaryIO, *, key: bytes) -> EnvelopeHeader:
    """Decrypt and write out, refusing anything that is not intact and complete.

    "Complete" is the word doing the work. A decryptor that returned what it could read would turn
    a partially-copied backup into a successful restore of partial data, and that failure is silent
    at every layer above it: PostgreSQL restores what it is given, the evidence store accepts what
    it is handed, and the first sign of trouble is a bundle that verifies against a manifest listing
    artifacts nobody can find.
    """
    header = read_header(source)
    header_bytes = header.as_bytes()
    prefix = bytes.fromhex(header.nonce_prefix)
    aesgcm = AESGCM(key)

    index = 0
    while True:
        raw_length = source.read(4)
        if len(raw_length) != 4:
            raise EnvelopeError(
                f"the backup ends after {index} chunk(s) without one marked final. It has been "
                "truncated: restoring from it would silently omit everything after that point."
            )
        (length,) = struct.unpack(">I", raw_length)
        if length > header.chunk_bytes + TAG_BYTES:
            raise EnvelopeError(f"chunk {index} declares an implausible length")
        sealed = source.read(length)
        if len(sealed) != length:
            raise EnvelopeError(f"truncated inside chunk {index}")

        # Tried as a non-final chunk first, then as the final one. Which one authenticates is the
        # answer -- a forger cannot flip the flag, because the flag is inside the AAD.
        for final in (False, True):
            try:
                plain = aesgcm.decrypt(
                    prefix + struct.pack(">Q", index),
                    sealed,
                    _aad(header_bytes, index, final=final),
                )
            except InvalidTag:
                continue
            destination.write(plain)
            if final:
                return header
            break
        else:
            raise EnvelopeError(
                f"chunk {index} does not authenticate. Either the key is wrong, or this chunk was "
                "altered, reordered, or spliced in from a different backup."
            )

        index += 1
        if index > MAX_CHUNKS:
            raise EnvelopeError(f"archive exceeds {MAX_CHUNKS} chunks")
