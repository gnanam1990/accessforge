"""Service attestation signatures, and what a signature does not mean.

Ed25519 over the canonical manifest bytes. The cryptography is the easy part; the honest part is the
framing, and CONTRACTS states it: "A manifest signature authenticates the service attestation,
not the truth of a physical user experience."

So every function here returns or requires a `TrustRoot` that says where the verifying key came
from,
and the verifier reports signature results in terms of *attribution* rather than *validity*. The
distinction is not pedantry. A reader who sees "signature valid" concludes the contents are true. A
reader who sees "attributed to issuer accessforge-service, key af-2026-09, trusted because you
supplied that key out of band" knows exactly what they have — and, more importantly, knows what they
would need in order to have more.

**An embedded public key is not provenance.** A bundle carrying its own verifying key proves only
that whoever made the bundle made the signature, which is true of a forgery too. `KeyProvenance`
distinguishes the two cases and the verifier reports them differently, because the alternative is a
green checkmark that means nothing.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import StrEnum

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class KeyProvenance(StrEnum):
    """How the reader came to hold the verifying key. This is the whole question.

    `EMBEDDED_IN_BUNDLE` is accepted and reported as what it is. Refusing it outright would make
    bundles unreadable without prior arrangement, which is worse; presenting it as trusted would
    be a
    lie. The verifier's report says which one applies.
    """

    EXTERNALLY_SUPPLIED = "EXTERNALLY_SUPPLIED"
    """The reader obtained the key independently of the bundle. This is the case that means
    something: a signature checked against it attributes the bundle to a known issuer."""

    EMBEDDED_IN_BUNDLE = "EMBEDDED_IN_BUNDLE"
    """The key travelled with the bundle. A valid signature then proves the bundle is internally
    consistent, and nothing about who produced it: a forger signs with a key and embeds it."""


@dataclass(frozen=True, slots=True)
class TrustRoot:
    """A verifying key, plus where it came from and who it is said to belong to."""

    key_id: str
    issuer: str
    public_key_base64: str
    provenance: KeyProvenance

    def public_key(self) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(self.public_key_base64))

    @property
    def independently_trusted(self) -> bool:
        return self.provenance is KeyProvenance.EXTERNALLY_SUPPLIED


@dataclass(frozen=True, slots=True)
class Attestation:
    """A signature and the key it was made with.

    `key_id` is part of the attestation rather than looked up at verification time, because rotation
    is the normal case: a bundle signed last month must stay checkable after the key changes, and a
    verifier that only knew the current key would report every older bundle as forged.
    """

    key_id: str
    issuer: str
    algorithm: str
    signature_base64: str

    @property
    def means(self) -> str:
        return (
            "This signature attributes the manifest to the named issuer. It does not make the "
            "manifest's contents true, does not establish that any person could use the "
            "application, and does not independently verify the events the evidence describes."
        )


class SigningKey:
    """A rotatable service signing key.

    Generation is here for tests and local development. A production key lives in a key management
    service and this class is constructed from its handle; nothing in the verifier needs the private
    half, which is the property that lets a bundle be checked by someone with no access to anything.
    """

    def __init__(self, private_key: Ed25519PrivateKey, *, key_id: str, issuer: str) -> None:
        self._private = private_key
        self.key_id = key_id
        self.issuer = issuer

    @classmethod
    def generate(cls, *, key_id: str, issuer: str) -> SigningKey:
        return cls(Ed25519PrivateKey.generate(), key_id=key_id, issuer=issuer)

    def trust_root(self, *, provenance: KeyProvenance) -> TrustRoot:
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )

        raw = self._private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return TrustRoot(
            key_id=self.key_id,
            issuer=self.issuer,
            public_key_base64=base64.b64encode(raw).decode("ascii"),
            provenance=provenance,
        )

    def sign(self, manifest_bytes: bytes) -> Attestation:
        return Attestation(
            key_id=self.key_id,
            issuer=self.issuer,
            algorithm="Ed25519",
            signature_base64=base64.b64encode(self._private.sign(manifest_bytes)).decode("ascii"),
        )


@dataclass(frozen=True, slots=True)
class AttestationCheck:
    """The result of checking a signature, phrased as attribution rather than validity."""

    signature_matches: bool
    attributed_to: str | None
    key_id: str
    independently_trusted: bool
    detail: str

    @property
    def summary(self) -> str:
        if not self.signature_matches:
            return f"SIGNATURE DOES NOT MATCH (key {self.key_id}): {self.detail}"
        if self.independently_trusted:
            return (
                f"attributed to {self.attributed_to} using key {self.key_id}, which you supplied "
                "independently of this bundle"
            )
        return (
            f"self-consistent signature by key {self.key_id}, which travelled inside the bundle. "
            "This attributes the bundle to nobody: a forger signs with their own key and embeds it."
        )


def check_attestation(
    *, manifest_bytes: bytes, attestation: Attestation, trust_root: TrustRoot
) -> AttestationCheck:
    """Check a signature and report what it establishes.

    A key-id mismatch is reported as a distinct failure rather than folded into "does not match".
    They send a reader somewhere different: a mismatched signature suggests tampering, while a
    mismatched key id usually means the reader holds the wrong key for a perfectly intact bundle.
    """
    if attestation.key_id != trust_root.key_id:
        return AttestationCheck(
            signature_matches=False,
            attributed_to=None,
            key_id=attestation.key_id,
            independently_trusted=trust_root.independently_trusted,
            detail=(
                f"the bundle was signed with key {attestation.key_id} and you supplied key "
                f"{trust_root.key_id}. This is most likely the wrong key for an intact bundle "
                "rather than a tampered bundle; obtain the named key from the issuer."
            ),
        )
    if attestation.algorithm != "Ed25519":
        return AttestationCheck(
            signature_matches=False,
            attributed_to=None,
            key_id=attestation.key_id,
            independently_trusted=trust_root.independently_trusted,
            detail=(
                f"unsupported signature algorithm {attestation.algorithm!r}. A verifier that "
                "accepted an unknown algorithm by skipping the check would report success on a "
                "bundle it never examined."
            ),
        )

    try:
        trust_root.public_key().verify(
            base64.b64decode(attestation.signature_base64), manifest_bytes
        )
    except InvalidSignature:
        return AttestationCheck(
            signature_matches=False,
            attributed_to=None,
            key_id=attestation.key_id,
            independently_trusted=trust_root.independently_trusted,
            detail="the manifest does not match the signature; it has been altered since signing",
        )

    return AttestationCheck(
        signature_matches=True,
        attributed_to=attestation.issuer,
        key_id=attestation.key_id,
        independently_trusted=trust_root.independently_trusted,
        detail=attestation.means,
    )
