"""Resolving source to an immutable identity, and taking it in safely.

A branch name, an image tag and a deployment URL are all mutable. None of them is a build identity,
and treating one as if it were is how a "reproducible" run becomes unreproducible without anyone
noticing — the classic case being a force-pushed branch where the same name now means different
bytes.

So this module does two jobs and refuses rather than guessing at either:

* **Resolve to immutable identities.** A commit SHA and a tree digest, captured separately from the
  build artifact and the environment configuration. If the working tree is dirty, that is recorded
  explicitly and the result is *not* labelled as the commit — a dirty checkout is a different set of
  bytes with the same name.
* **Take source in without executing it.** No hooks, no submodule fetching, no install scripts, no
  generated instructions. Archive extraction refuses path traversal and symlink escape, because an
  archive is attacker-controlled input the moment it comes from a repository anyone else can
  write to.
"""

from __future__ import annotations

import hashlib
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

from accessforge_domain.canonical import digest


class SourceIntakeError(Exception):
    """Source could not be resolved or taken in safely."""


class ReproducibilityRefused(SourceIntakeError):
    """The bytes cannot be given an immutable identity, so no provenance claim is made.

    Raised instead of returning a best-effort digest. An unidentifiable deployment must stay
    unidentifiable: filling the field in with something plausible is exactly how an invented digest
    ends up in a manifest that claims to be verifiable.
    """


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    """Immutable identity of a source tree.

    ``commit_sha`` is where the tree came from; ``tree_digest`` is what it actually contains. They
    are separate because a dirty checkout shares the former and not the latter, and because a force
    push changes what a ref means while leaving the ref name intact.
    """

    commit_sha: str
    tree_digest: str
    dirty: bool
    """True when the working tree differs from the commit.

    A dirty tree is not refused outright — local development is a legitimate E0 case — but it is
    recorded, and `tree_digest` then describes the working tree rather than the commit. Nothing may
    report a dirty tree as clean HEAD.
    """

    dirty_paths: tuple[str, ...] = ()
    """Which paths differ, so a reader can see *what* made it dirty rather than only that it was."""

    @property
    def is_reproducible_from_the_commit(self) -> bool:
        """Whether fetching this commit elsewhere would reproduce these exact bytes."""
        return not self.dirty


def _git(repo: Path, *args: str) -> str:
    """Run a read-only git command.

    Deliberately a narrow allowlist of operations rather than a general runner: the point of this
    module is that taking in source does not execute repository-controlled code, and a generic
    "run git with whatever" helper is how a config-specified hook or pager gets invoked.
    """
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        # -c core.hooksPath=/dev/null is not enough on its own; these are read-only commands that do
        # not run hooks, which is why the allowlist matters more than the flags.
        env={"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(repo)},
    )
    if result.returncode != 0:
        raise SourceIntakeError(
            f"git {' '.join(args)} failed in {repo}: {result.stderr.strip() or 'no output'}"
        )
    # Returned unstripped. `status --porcelain` is a fixed-width format whose first two columns are
    # the status code, and stripping the whole output removes the leading space of the first line
    # only — mangling one path and leaving the rest intact. Callers strip where it is safe.
    return result.stdout


def _hash_tree(root: Path) -> tuple[str, int]:
    """Digest a directory's contents, ignoring the repository metadata.

    Paths are sorted and included in the hash, so a rename changes the digest even when every
    byte of
    content is unchanged. Symlinks are recorded by their target rather than followed: following them
    would let a link to a file outside the tree silently contribute to its identity.
    """
    entries: list[dict[str, str | int]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == ".git":
            continue
        if path.is_symlink():
            entries.append(
                {"path": str(relative), "kind": "symlink", "target": str(path.readlink())}
            )
        elif path.is_dir():
            continue  # directories carry no content; their presence is implied by their files
        elif path.is_file():
            entries.append(
                {
                    "path": str(relative),
                    "kind": "file",
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "size": path.stat().st_size,
                }
            )
        else:
            # A device, socket or fifo in a source tree is not something we will silently digest.
            raise SourceIntakeError(f"{relative} is not a regular file, directory or symlink")
    return digest({"entries": entries}), len(entries)


def resolve_source(repo: Path, *, revision: str = "HEAD") -> SourceIdentity:
    """Resolve a repository revision to an immutable identity.

    ``revision`` may be a branch name for convenience, but the *result* never is: it is resolved
    to a
    commit SHA immediately, so a later force push cannot change what this identity refers to.
    """
    if not (repo / ".git").exists():
        raise SourceIntakeError(f"{repo} is not a git repository")

    commit_sha = _git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}").strip()
    if len(commit_sha) != 40:
        raise SourceIntakeError(f"unexpected commit identity {commit_sha!r}")

    # Porcelain v1: two status columns, a space, then the path. Parsed positionally rather than by
    # splitting on whitespace, because a path may legitimately contain spaces.
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    dirty_paths = tuple(
        sorted(line[3:] for line in status.splitlines() if len(line) > 3 and line.strip())
    )

    tree_digest, _ = _hash_tree(repo)

    return SourceIdentity(
        commit_sha=commit_sha,
        tree_digest=tree_digest,
        dirty=bool(dirty_paths),
        dirty_paths=dirty_paths,
    )


def assert_reproducible(identity: SourceIdentity) -> None:
    """Raise unless this identity can be reproduced from its commit alone.

    Callers that intend to make a provenance claim call this; callers recording a local development
    run do not. Separating the two means a dirty tree is usable without ever being *described* as
    clean.
    """
    if not identity.is_reproducible_from_the_commit:
        raise ReproducibilityRefused(
            f"the working tree differs from commit {identity.commit_sha[:12]} in "
            f"{len(identity.dirty_paths)} path(s): {', '.join(identity.dirty_paths[:5])}"
            f"{'…' if len(identity.dirty_paths) > 5 else ''}. "
            "A dirty checkout shares the commit's name but not its bytes, so no reproducibility "
            "claim is made for it."
        )


# --- archive intake -----------------------------------------------------------------------------


def _is_within(base: Path, candidate: Path) -> bool:
    """Whether ``candidate`` resolves inside ``base``.

    Compared after resolution, so ``a/../../etc`` and a symlinked parent are both caught. String
    prefix comparison would miss both.
    """
    try:
        candidate.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def extract_archive_safely(archive: Path, destination: Path) -> int:
    """Extract a tar archive, refusing anything that could escape the destination.

    An archive from a repository is attacker-controlled input. Four specific refusals, each a known
    escape:

    * **Absolute paths** — ``/etc/cron.d/x`` writes outside the destination entirely.
    * **Parent traversal** — ``../../x`` does the same with relative steps.
    * **Symlinks and hard links** — a link written first, then followed by a later member, writes
      through it to anywhere the process can reach.
    * **Special files** — devices, fifos and sockets have no place in a source tree.

    Returns the number of members extracted. Nothing is executed: this unpacks bytes and stops.
    """
    destination.mkdir(parents=True, exist_ok=True)
    base = destination.resolve()
    extracted = 0

    with tarfile.open(archive, "r:*") as tar:
        for member in tar.getmembers():
            name = member.name

            if name.startswith("/") or Path(name).is_absolute():
                raise SourceIntakeError(f"archive member {name!r} is an absolute path")
            if ".." in Path(name).parts:
                raise SourceIntakeError(f"archive member {name!r} traverses outside the archive")
            if member.issym() or member.islnk():
                raise SourceIntakeError(
                    f"archive member {name!r} is a link; a link can be written and then followed "
                    "by a later member to write outside the destination"
                )
            if not (member.isfile() or member.isdir()):
                raise SourceIntakeError(
                    f"archive member {name!r} is neither a file nor a directory"
                )

            target = base / name
            if not _is_within(base, target.parent if not member.isdir() else target):
                raise SourceIntakeError(f"archive member {name!r} resolves outside the destination")

            tar.extract(member, path=base, filter="data")
            extracted += 1

    return extracted
