"""Reading an untrusted archive safely.

A bundle arrives as a file from outside. Everything in this module treats it as hostile, because the
alternative is a verifier that can be attacked by the thing it was asked to check — and a security
tool that is exploitable by its input is worse than no tool, since it is run on exactly the files
nobody trusts.

The five things an archive can do to a naive reader, and what happens here instead:

**Escape the extraction directory.** `../../.ssh/authorized_keys`, an absolute path, a symlink
pointing outside. Nothing here extracts to disk at all — entries are read into memory by name from a
closed allowlist — so there is no directory to escape.

**Exhaust memory by decompressing.** A few kilobytes of zeros inflate to gigabytes. Every read is
bounded *before* decompression completes, by reading a capped number of bytes rather than asking for
the whole member.

**Hide a second entry behind a first.** Two members with the same name, where a validator checks one
and a consumer reads the other. Duplicate names are refused outright.

**Carry something executable.** Nothing in a bundle is ever run, and the allowlist admits only the
document names the format defines.

**Be enormous.** Both the archive and each member are capped.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass

#: The complete set of member names a bundle may contain. An allowlist, so an unexpected member is
#: refused rather than ignored: a reader that ignored extra entries would let a bundle carry
#: anything at all, and "we never look at it" is a property of today's code, not of the format.
ALLOWED_MEMBERS: frozenset[str] = frozenset(
    {"bundle.json", "manifest.canonical.json", "attestation.json", "artifacts/"}
)

#: Caps. Generous enough for a real transcript bundle and nowhere near enough to exhaust a machine.
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_MEMBERS = 256

#: Ratio above which a member is treated as a decompression bomb rather than a well-compressed file.
#: JSON and text legitimately reach 20:1; 200:1 is a file of zeros.
MAX_COMPRESSION_RATIO = 200


class UnsafeArchive(Exception):
    """The archive was refused before anything was read out of it."""


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    name: str
    data: bytes


def _assert_member_name_safe(name: str) -> None:
    """Refuse any name that is not a plain relative path under an allowed prefix.

    The checks are stated positively — must match an allowed name or prefix — rather than as a
    list of
    dangerous patterns. A denylist here has to anticipate every encoding of "go up a directory" on
    every platform, which is the losing side of that problem.
    """
    if name in ALLOWED_MEMBERS:
        return
    if name.startswith("artifacts/") and name != "artifacts/":
        tail = name[len("artifacts/") :]
        if "/" in tail or "\\" in tail:
            raise UnsafeArchive(
                f"member {name!r} nests below artifacts/. The format is flat there, and a nested "
                "path is the shape a traversal takes."
            )
        if tail in {".", ".."} or tail.startswith("."):
            raise UnsafeArchive(f"member {name!r} is not a plain file name")
        if not all(c.isalnum() or c in "._-" for c in tail):
            raise UnsafeArchive(
                f"member {name!r} contains characters outside alphanumerics, dot, underscore and "
                "hyphen. Artifact members are named by digest, so anything else is not one."
            )
        return
    raise UnsafeArchive(
        f"member {name!r} is not part of the bundle format. The permitted members are "
        f"{', '.join(sorted(ALLOWED_MEMBERS))} and artifacts/<name>. An unexpected member is "
        "refused rather than ignored: ignoring it would let a bundle carry anything at all."
    )


def read_archive(path: str) -> dict[str, bytes]:
    """Read a bundle archive into memory, or refuse it.

    Returns member name to bytes. Nothing is written to disk, which removes traversal and symlink
    attacks by construction rather than by validation.
    """
    import os

    size = os.path.getsize(path)
    if size > MAX_ARCHIVE_BYTES:
        raise UnsafeArchive(f"archive is {size} bytes; the limit is {MAX_ARCHIVE_BYTES}")

    members: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_MEMBERS:
                raise UnsafeArchive(f"{len(infos)} members; the limit is {MAX_MEMBERS}")

            seen: set[str] = set()
            for info in infos:
                name = info.filename
                if name in seen:
                    # Two members with one name is how a validator is made to check a different file
                    # from the one a consumer reads.
                    raise UnsafeArchive(
                        f"member {name!r} appears twice. A duplicate name lets a validator "
                        "check one entry while a consumer reads the other."
                    )
                seen.add(name)

                if name.endswith("/"):
                    _assert_member_name_safe(name)
                    continue
                _assert_member_name_safe(name)

                if info.file_size > MAX_MEMBER_BYTES:
                    raise UnsafeArchive(
                        f"member {name!r} declares {info.file_size} bytes; the limit is "
                        f"{MAX_MEMBER_BYTES}"
                    )
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > MAX_COMPRESSION_RATIO:
                        raise UnsafeArchive(
                            f"member {name!r} expands {ratio:.0f}:1. JSON and text reach about "
                            f"20:1; beyond {MAX_COMPRESSION_RATIO}:1 this is a file of zeros "
                            "rather than a well-compressed document."
                        )

                with archive.open(info) as handle:
                    # Bounded read of one extra byte, so an entry whose declared size understates
                    # its real size is caught during decompression rather than trusted from the
                    # header. The declared size is attacker-controlled.
                    data = handle.read(MAX_MEMBER_BYTES + 1)
                if len(data) > MAX_MEMBER_BYTES:
                    raise UnsafeArchive(
                        f"member {name!r} decompressed past {MAX_MEMBER_BYTES} bytes despite "
                        "declaring less; the declared size is not to be trusted"
                    )
                members[name] = data
    except zipfile.BadZipFile as exc:
        raise UnsafeArchive(f"not a readable archive: {exc}") from exc

    for required in ("bundle.json", "manifest.canonical.json", "attestation.json"):
        if required not in members:
            raise UnsafeArchive(f"the archive has no {required}")
    return members


def write_archive(path: str, members: dict[str, bytes]) -> None:
    """Write a bundle archive. Every member name passes the same check a reader applies.

    Checked on the way out as well as in, so a bug in bundle assembly produces a failure here rather
    than an archive that this project's own verifier would refuse.
    """
    for name in members:
        _assert_member_name_safe(name)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(members):
            archive.writestr(name, members[name])
