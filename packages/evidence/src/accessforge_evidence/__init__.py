"""Evidence bundles and the offline verifier.

An export is where evidence leaves the system, and the question that shapes everything here is not
what to include but what a reader can conclude from what they hold. Three claims stay separate
throughout, because outside this package they are routinely collapsed into one:

* the bytes are intact — digests match, checkable by anyone;
* the service attests this — a signature attributing the manifest to an issuer;
* a person could use the application — established by no bundle, ever.

`TrustLevel` is an enum rather than a boolean for that reason, and the verifier reports a list of
findings rather than a verdict. A redacted bundle is not a failed bundle, an unchecked signature is
not a passed one, and a bundle in a format this verifier does not know is neither.
"""

from .archive import (
    ALLOWED_MEMBERS,
    MAX_ARCHIVE_BYTES,
    MAX_COMPRESSION_RATIO,
    MAX_MEMBER_BYTES,
    UnsafeArchive,
    read_archive,
    write_archive,
)
from .bundle import (
    BUNDLE_SCHEMA_VERSION,
    CANONICALIZATION_VERSION,
    ArtifactEntry,
    Bundle,
    CompletenessDeclaration,
    EvidenceState,
    ProducerEntry,
    ReviewEntry,
    TrustLevel,
    to_json,
)
from .signing import (
    Attestation,
    AttestationCheck,
    KeyProvenance,
    SigningKey,
    TrustRoot,
    check_attestation,
)
from .verifier import CheckOutcome, Finding, VerificationReport, verify_archive

__all__ = [
    "ALLOWED_MEMBERS",
    "BUNDLE_SCHEMA_VERSION",
    "CANONICALIZATION_VERSION",
    "MAX_ARCHIVE_BYTES",
    "MAX_COMPRESSION_RATIO",
    "MAX_MEMBER_BYTES",
    "ArtifactEntry",
    "Attestation",
    "AttestationCheck",
    "Bundle",
    "CheckOutcome",
    "CompletenessDeclaration",
    "EvidenceState",
    "Finding",
    "KeyProvenance",
    "ProducerEntry",
    "ReviewEntry",
    "SigningKey",
    "TrustLevel",
    "TrustRoot",
    "UnsafeArchive",
    "VerificationReport",
    "check_attestation",
    "read_archive",
    "to_json",
    "verify_archive",
    "write_archive",
]
