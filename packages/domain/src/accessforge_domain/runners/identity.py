"""What "the same desktop" means, and what it deliberately is not.

CONTRACTS section 5: "One active lease per physical interactive desktop session." The word doing
the work is *physical*. A supervisor can be restarted, containerized, renamed or run twice, and
none of that creates a second screen. If lease admission keyed on anything the supervisor chooses
for itself, running it twice would defeat the invariant it exists to enforce (INV-10).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from accessforge_domain.canonical import digest

# The platforms a physical interactive session can be identified on. A runner is a signed-in
# desktop; there is no third option, and "linux" is absent because neither VoiceOver nor NVDA runs
# there. A future platform is a contract change, not a value someone passes in.
SUPPORTED_PLATFORMS = frozenset({"darwin", "win32"})

# Identifiers that name a *process* rather than a *desktop*. Accepting any of these as session
# identity is the specific mistake this module exists to prevent, so they are refused by name
# rather than left to reviewer vigilance.
_PROCESS_SHAPED_KEYS = frozenset(
    {
        "pid",
        "process_id",
        "processid",
        "container_id",
        "containerid",
        "docker_id",
        "task_arn",
        "instance_id",
        "hostname",
        "boot_id",
    }
)

_SESSION_ID_SHAPE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class EnrollmentError(ValueError):
    """A runner presented an identity or a profile that cannot be trusted to mean what it says."""


@dataclass(frozen=True, slots=True)
class PhysicalSession:
    """The operating system's own name for one signed-in interactive desktop.

    ``platform`` selects which identifier is authoritative:

    * ``darwin`` — the audit session identifier of the console session (``auid``), which is stable
      for the life of a login and is not inherited by a process started from another session.
    * ``win32`` — the Windows Terminal Services session id, likewise per interactive logon.

    ``device_id`` scopes that identifier to a machine: session id ``1`` exists on every Windows
    host in the world, and two machines both reporting session 1 are two desktops.

    ``console`` records whether this is the physically attended console session or a remote one.
    A remote session shares a machine but not a screen, and is a different desktop for our purpose;
    it is part of the key rather than a note on the side.
    """

    device_id: str
    platform: str
    interactive_session_id: str
    console: bool

    def __post_init__(self) -> None:
        if self.platform not in SUPPORTED_PLATFORMS:
            raise EnrollmentError(
                f"platform {self.platform!r} is not a supported interactive desktop; expected one "
                f"of {sorted(SUPPORTED_PLATFORMS)}. A screen reader run is a signed-in desktop "
                "session, and no other platform provides one here."
            )
        for name in ("device_id", "interactive_session_id"):
            value = getattr(self, name)
            if not _SESSION_ID_SHAPE.match(value):
                raise EnrollmentError(
                    f"{name} {value!r} is not a usable identifier; it must be 1-128 characters of "
                    "alphanumerics, dot, colon, underscore or hyphen"
                )

    @property
    def key(self) -> str:
        """The exclusivity key. Two runners sharing this share a screen."""
        return session_key(self)


def session_key(session: PhysicalSession) -> str:
    """A single opaque string naming one physical desktop.

    A digest rather than a formatted string: this value is a database key and appears in audit
    records, and `device-1|darwin|100|True` invites someone to parse it back apart and reason about
    the pieces. The pieces are already available on the dataclass.
    """
    return digest(
        {
            "console": session.console,
            "deviceId": session.device_id,
            "interactiveSessionId": session.interactive_session_id,
            "platform": session.platform,
        }
    )


@dataclass(frozen=True, slots=True)
class RunnerProfile:
    """What this desktop can actually do, as claimed at enrollment.

    Claimed, not proven. A profile is an application to be checked: preflight is what turns any of
    this into evidence, and :mod:`accessforge_domain.runners.preflight` refuses to let a claim
    stand in for an observation. A runner that says it has VoiceOver 10 and cannot produce a speech
    observation is not a VoiceOver runner, it is a quarantine case.
    """

    platform: str
    reader_name: str
    reader_version: str
    browser_name: str
    browser_version: str
    locale: str
    keyboard_layout: str

    def __post_init__(self) -> None:
        if self.platform not in SUPPORTED_PLATFORMS:
            raise EnrollmentError(f"platform {self.platform!r} is not a supported desktop platform")
        for name in (
            "reader_name",
            "reader_version",
            "browser_name",
            "browser_version",
            "locale",
            "keyboard_layout",
        ):
            if not str(getattr(self, name)).strip():
                raise EnrollmentError(
                    f"{name} is empty. An unstated {name.replace('_', ' ')} cannot be matched "
                    "against a journey's capability requirements, and an unmatched requirement "
                    "must be visible rather than assumed compatible"
                )

    @property
    def digest(self) -> str:
        return profile_digest(self)


def profile_digest(profile: RunnerProfile) -> str:
    """The `runnerProfileDigest` that binds an outcome to the exact environment (INV-03).

    Every field participates. A locale or keyboard-layout change alters what a screen reader
    announces and which key chords reach the browser, so a run recorded under one profile says
    nothing about another.
    """
    return digest(
        {
            "browserName": profile.browser_name,
            "browserVersion": profile.browser_version,
            "keyboardLayout": profile.keyboard_layout,
            "locale": profile.locale,
            "platform": profile.platform,
            "readerName": profile.reader_name,
            "readerVersion": profile.reader_version,
        }
    )


def assert_not_process_identity(claimed: dict[str, object]) -> None:
    """Refuse an enrollment that offers a process identity where a desktop identity is required.

    This is a deliberate belt-and-braces check on top of :class:`PhysicalSession`'s typed fields.
    The failure it guards against is not a type error; it is someone plumbing a container id into
    ``interactive_session_id`` because it was the identifier that happened to be in scope, which
    type-checks perfectly and silently permits two attempts on one screen.
    """
    offending = sorted(k for k in claimed if k.lower() in _PROCESS_SHAPED_KEYS)
    if offending:
        raise EnrollmentError(
            f"session identity was given {', '.join(offending)}, which name a process or a host "
            "rather than an interactive desktop. Restarting the supervisor, or running a second "
            "copy of it, would produce a different value and admit a second attempt to the same "
            "screen. Use the operating system's interactive session identifier."
        )
