"""Check that this machine can actually run AccessForge, before anything is started.

The failure this prevents is not "a tool is missing". It is the *shape* of the report when several
things are missing at once: a bootstrap that stops at the first problem makes an operator fix one
thing, re-run, fix the next, and re-run again, which is how a ten-minute setup becomes an
afternoon. So every check runs and every result is printed, and the exit status summarises.

Three rules this script keeps, each of which is easy to break by being helpful:

**It never prints a secret.** A credential is reported as present or absent and never echoed, not
even partially. A masked prefix is still an oracle, and a doctor's output is the single most likely
thing in this repository to be pasted into an issue.

**It never installs anything.** No download, no `pip install`, no fetching a binary from a URL.
Installing on the operator's behalf means executing code chosen by whatever the network returned,
and a bootstrap script is exactly where nobody looks. Every remedy is printed as a command for a
person to read and run.

**It distinguishes "not installed" from "installed and wrong" from "cannot be installed".**
The three have different remedies, and a platform capability -- VoiceOver, NVDA, a Bedrock
entitlement -- belongs in the third. Reporting a blocked capability as a missing dependency invites
somebody to try to install their way out of a permissions grant that only a human can give.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import subprocess  # noqa: S404 - probing local toolchain versions is this script's purpose
import sys
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_MATRIX = REPO_ROOT / "infra" / "version-matrix.toml"


class Verdict(StrEnum):
    OK = "OK"
    MISSING = "MISSING"
    WRONG_VERSION = "WRONG VERSION"
    OUT_OF_DATE = "OUT OF DATE"
    """The thing is present and correct for an earlier release. Distinct from WRONG_VERSION
    because the remedy is to run something rather than to install something."""

    MISCONFIGURED = "MISCONFIGURED"
    UNREACHABLE = "UNREACHABLE"
    BLOCKED = "BLOCKED"
    """A capability no installation can supply: a permissions grant, a physical machine, an
    entitlement on somebody's account. Reported separately from MISSING because the remedy is a
    person, not a package manager."""

    NOT_CHECKED = "NOT CHECKED"


#: Which verdicts mean "this machine cannot run the local product path". BLOCKED is not among them:
#: every blocked capability in the matrix is one the local path already declares it cannot exercise,
#: and failing the doctor on them would make a correct development machine look broken.
_FATAL = frozenset(
    {
        Verdict.MISSING,
        Verdict.WRONG_VERSION,
        Verdict.OUT_OF_DATE,
        Verdict.MISCONFIGURED,
        Verdict.UNREACHABLE,
    }
)


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    verdict: Verdict
    found: str
    remedy: str = ""

    def render(self) -> str:
        line = f"  [{self.verdict:^13}] {self.name:<34} {self.found}"
        if self.remedy and self.verdict is not Verdict.OK:
            line += f"\n{'':>18}-> {self.remedy}"
        return line


def _version_of(probe: list[str]) -> str | None:
    """Run a `--version` probe, or return None if the tool is not on PATH.

    `shutil.which` first so that a missing tool is reported as missing rather than as a
    FileNotFoundError from the exec, and so nothing is executed from a relative path.
    """
    if shutil.which(probe[0]) is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603 - argv comes from the committed version matrix
            probe, capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (out.stdout + out.stderr).strip()


def _first_version(text: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return ()
    return tuple(int(g) for g in match.groups() if g is not None)


def _as_tuple(spec: str) -> tuple[int, ...]:
    return tuple(int(part) for part in spec.split("."))


def _check_tool(name: str, spec: dict[str, Any]) -> Check:
    probe = list(spec["probe"])
    raw = _version_of(probe)
    if raw is None:
        return Check(
            name,
            Verdict.MISSING,
            "not on PATH",
            f"install {name} and re-run; this script does not download anything",
        )

    found = _first_version(raw)
    if not found:
        return Check(name, Verdict.WRONG_VERSION, f"unparseable output: {raw.splitlines()[0]}")

    if "exact" in spec:
        want = _as_tuple(spec["exact"])
        ok = found[: len(want)] == want
        expected = spec["exact"]
    else:
        low = _as_tuple(spec["min"])
        ok = found[: len(low)] >= low
        expected = f">= {spec['min']}"
        if "below" in spec:
            high = _as_tuple(spec["below"])
            ok = ok and found[: len(high)] < high
            expected = f">= {spec['min']}, < {spec['below']}"

    shown = ".".join(str(p) for p in found)
    if ok:
        return Check(name, Verdict.OK, f"{shown} (want {expected})")
    return Check(
        name,
        Verdict.WRONG_VERSION,
        f"{shown} (want {expected})",
        "this version was not tested; the matrix in infra/version-matrix.toml is widened by "
        "running the suite on the wider range, not by editing the number",
    )


#: Every variable the control plane requires, and what it is for. Names only -- values are never
#: read into the report.
_REQUIRED_ENV = {
    "ACCESSFORGE_DATABASE_URL": "authoritative business state",
    "ACCESSFORGE_EVIDENCE_ENDPOINT_URL": "S3-compatible evidence storage",
    "ACCESSFORGE_EVIDENCE_BUCKET": "evidence bucket name",
    "ACCESSFORGE_EVIDENCE_ACCESS_KEY": "evidence storage credential",
    "ACCESSFORGE_EVIDENCE_SECRET_KEY": "evidence storage credential",
}

_PLACEHOLDER = ("changeme", "placeholder", "replace_me", "replaceme", "your_", "xxxx")


def _check_environment(env: dict[str, str]) -> list[Check]:
    checks: list[Check] = []
    for name, purpose in _REQUIRED_ENV.items():
        value = env.get(name, "")
        if not value:
            checks.append(
                Check(
                    name,
                    Verdict.MISSING,
                    f"unset ({purpose})",
                    "copy .env.example to .env and fill it in; startup fails closed on "
                    "placeholder values",
                )
            )
        elif any(marker in value.lower() for marker in _PLACEHOLDER):
            # The value is inspected and never printed. Saying which marker matched would echo part
            # of the value, and the operator does not need it to know what to do.
            checks.append(
                Check(
                    name,
                    Verdict.MISSING,
                    "still set to an example placeholder",
                    "replace the placeholder with a real value",
                )
            )
        else:
            checks.append(Check(name, Verdict.OK, f"set ({purpose})"))
    return checks


def _check_postgres(database_url: str) -> list[Check]:
    """Reachable, isolating, and at a schema this build can serve. Three separate answers."""
    if not database_url:
        return [Check("postgresql", Verdict.NOT_CHECKED, "no database url configured")]

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:  # pragma: no cover - the workspace always installs psycopg
        return [Check("postgresql", Verdict.MISSING, "psycopg not importable", "uv sync --frozen")]

    try:
        with psycopg.connect(database_url, connect_timeout=5, row_factory=dict_row) as conn:
            server = conn.execute("SHOW server_version").fetchone()
            role = conn.execute(
                "SELECT current_user AS role, rolsuper, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            ).fetchone()
    except psycopg.Error as exc:
        # Type name only. A psycopg connection error embeds the connection string, password and all.
        return [
            Check(
                "postgresql",
                Verdict.UNREACHABLE,
                type(exc).__name__,
                "start PostgreSQL and confirm ACCESSFORGE_DATABASE_URL points at it",
            )
        ]

    if server is None or role is None:  # pragma: no cover - both queries always return a row
        return [Check("postgresql", Verdict.UNREACHABLE, "the server answered no rows")]

    checks = [Check("postgresql", Verdict.OK, f"server_version {server['server_version']}")]

    if role["rolsuper"] or role["rolbypassrls"]:
        checks.append(
            Check(
                "postgresql role",
                Verdict.MISCONFIGURED,
                f"{role['role']} bypasses row-level security",
                "tenant isolation is row-level security; a bypassing role has none. Create the "
                "application role with NOSUPERUSER NOBYPASSRLS.",
            )
        )
    else:
        checks.append(
            Check("postgresql role", Verdict.OK, f"{role['role']} is subject to row-level security")
        )

    try:
        from accessforge_persistence import applied_migrations, expected_migrations

        expected = expected_migrations()
        done = set(applied_migrations(database_url))
        pending = [name for name in expected if name not in done]
        unknown = sorted(done - set(expected))
        if unknown:
            checks.append(
                Check(
                    "schema",
                    Verdict.WRONG_VERSION,
                    f"{len(unknown)} migration(s) this build does not know",
                    "this database was written by a newer release; bring the code forward rather "
                    "than serving newer data from older code",
                )
            )
        elif pending:
            checks.append(
                Check(
                    "schema",
                    Verdict.OUT_OF_DATE,
                    f"{len(pending)} migration(s) pending",
                    "uv run python scripts/migrate.py",
                )
            )
        else:
            checks.append(Check("schema", Verdict.OK, f"at {expected[-1]}"))
    except Exception as exc:  # noqa: BLE001 - a doctor reports, it does not propagate
        checks.append(Check("schema", Verdict.UNREACHABLE, type(exc).__name__))

    return checks


def _check_object_store(endpoint: str) -> Check:
    """TCP reachability only, and the report says so.

    Whether the bucket exists and the credentials work is answered by writing an object, which a
    doctor must not do: a diagnostic that writes to the evidence store is a diagnostic that can
    leave objects behind in a store whose retention rules module 26 owns.
    """
    if not endpoint:
        return Check("evidence store", Verdict.NOT_CHECKED, "no endpoint configured")
    parsed = urlsplit(endpoint)
    if not parsed.hostname:
        return Check("evidence store", Verdict.MISSING, "unparseable endpoint url")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=5):
            pass
    except OSError as exc:
        return Check(
            "evidence store",
            Verdict.UNREACHABLE,
            type(exc).__name__,
            "start the object store; there is no filesystem fallback, by design",
        )
    return Check(
        "evidence store", Verdict.OK, "tcp-reachable (credentials and bucket not verified here)"
    )


def _check_capabilities(matrix: dict[str, Any]) -> list[Check]:
    checks = []
    for name, spec in matrix.get("capability", {}).items():
        applies = spec["platform"] in ("any", sys.platform)
        detail = spec["needs"].strip().replace("\n", " ")
        if not applies:
            checks.append(
                Check(
                    name,
                    Verdict.NOT_CHECKED,
                    f"requires {spec['platform']}; this host is {sys.platform}",
                    detail,
                )
            )
        else:
            checks.append(
                Check(name, Verdict(spec["status"]), "not established on this host", detail)
            )
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check this machine can run AccessForge.")
    parser.add_argument(
        "--skip-services",
        action="store_true",
        help="toolchain and configuration only; do not open connections",
    )
    args = parser.parse_args(argv)

    matrix = tomllib.loads(VERSION_MATRIX.read_text(encoding="utf-8"))

    # .env is read the same way the application reads it, so the doctor and the process it is
    # diagnosing disagree about nothing. Real environment variables win, as they do at startup.
    env: dict[str, str] = {}
    dotenv = REPO_ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            env[key.strip()] = value.strip()
    env.update(os.environ)

    sections: list[tuple[str, list[Check]]] = []

    toolchain = [_check_tool(n, s) for n, s in matrix["toolchain"].items()]
    services = [_check_tool(n, s) for n, s in matrix["service"].items()]
    sections.append(("Toolchain", toolchain))
    sections.append(("Service binaries", services))
    sections.append(("Configuration", _check_environment(env)))

    if args.skip_services:
        sections.append(
            (
                "Services",
                [Check("postgresql", Verdict.NOT_CHECKED, "--skip-services")],
            )
        )
    else:
        sections.append(
            (
                "Services",
                [
                    *_check_postgres(env.get("ACCESSFORGE_DATABASE_URL", "")),
                    _check_object_store(env.get("ACCESSFORGE_EVIDENCE_ENDPOINT_URL", "")),
                ],
            )
        )

    sections.append(("Platform capabilities", _check_capabilities(matrix)))

    for title, checks in sections:
        print(f"\n{title}")
        for check in checks:
            print(check.render())

    every = [c for _, checks in sections for c in checks]
    fatal = [c for c in every if c.verdict in _FATAL]
    blocked = [c for c in every if c.verdict is Verdict.BLOCKED]

    print()
    if blocked:
        print(
            f"{len(blocked)} capability/capabilities are BLOCKED and cannot be installed: "
            + ", ".join(c.name for c in blocked)
        )
        print("  The local product path does not exercise these. Any claim that does is false.")
    if fatal:
        print(f"\n{len(fatal)} problem(s) must be fixed before the local product path will run:")
        for check in fatal:
            print(f"  - {check.name}: {check.found}")
        return 1

    print("This machine can run the local product path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
