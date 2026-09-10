"""The operator CLI. It mirrors lifecycle operations and cannot bypass server policy.

That second clause is the design. Every check this system performs — membership, permission,
revision, quota, idempotency, whether a manifest is sealed — happens on the server, and this CLI
reaches the same routes a browser does. There is no `--force`, no direct database mode, and no
local validation that skips a round trip. A client-side check that disagreed with the server would
be a second, wrong definition of the rules, and the wrong one is always the one somebody trusts.

The output is shaped by one rule: **never print something that reads like an outcome when it is
not.** A run request prints "requested" and the operation id, because that is what happened. A run
whose status is RUNNING prints RUNNING. Nothing here prints a verdict the server did not give, and
nothing collapses "no result yet" into a blank that looks like success.

    accessforge sign-in --email you@example.test
    accessforge project list --workspace WS
    accessforge journey list --workspace WS --project P
    accessforge run request --workspace WS --project P --manifest DIGEST
    accessforge run show --workspace WS --run R
    accessforge runner list --workspace WS
    accessforge export verify BUNDLE.zip --key trust-root.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .client import AccessForgeClient, ApiProblem, Requested, Session

DEFAULT_BASE_URL = "http://127.0.0.1:8080"

#: Where a session is kept between invocations. A file rather than an environment variable, because
#: an environment variable holding a session token is inherited by every child process a shell
#: starts, and mode 600 on a file is a boundary the operating system enforces.
SESSION_PATH = Path(os.environ.get("ACCESSFORGE_CLI_SESSION", "~/.accessforge/session.json"))


def _client(args: argparse.Namespace, *, require_session: bool = True) -> AccessForgeClient:
    session = _load_session()
    if session is None and require_session:
        raise SystemExit(
            "not signed in. Run: accessforge sign-in --email you@example.test\n"
            "A deployment configured with ACCESSFORGE_IDENTITY_PROVIDER=none cannot sign anyone "
            "in, and will say so."
        )
    return AccessForgeClient(args.base_url, session=session)


def _load_session() -> Session | None:
    path = SESSION_PATH.expanduser()
    if not path.exists():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        return Session(
            session_token=str(stored["sessionToken"]),
            csrf_token=str(stored["csrfToken"]),
            user_id=str(stored.get("userId", "")),
        )
    except (ValueError, KeyError):
        # A corrupt file is treated as no session rather than as an error. The remedy is the same
        # either way, and failing here would leave somebody unable to sign in without deleting a
        # file they do not know about.
        return None


def _store_session(session: Session) -> None:
    """Write the session so it is never, for any instant, readable by anyone else.

    The obvious version writes the file and then chmods it to 600. Between those two calls the file
    exists at whatever the umask allows — 644 on a default configuration — and a session token
    world-readable for a millisecond on a shared machine is a session token world-readable. The
    window is small and it is not zero, and a test that checks the mode afterwards cannot see it.

    So: create with 0o600 in the `open` call, write, then rename over the target. `os.replace` is
    atomic within a filesystem, which also means a crash part-way through leaves the previous
    session intact rather than a truncated file that parses as no session at all.
    """
    path = SESSION_PATH.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    # The directory too: a 600 file inside a 755 directory still lets anyone list the filename and,
    # more to the point, replace it.
    path.parent.chmod(0o700)

    payload = json.dumps(
        {
            "sessionToken": session.session_token,
            "csrfToken": session.csrf_token,
            "userId": session.user_id,
        }
    )
    staging = path.with_name(f".{path.name}.{os.getpid()}")
    descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise


def _emit(value: Any) -> None:
    """Print a result, and print a `Requested` as a request.

    The 202 branch exists so that no script can read this output as a completion. `jq -r .outcome`
    on a run request returns nothing useful, which is correct: there is no outcome yet.
    """
    if isinstance(value, Requested):
        print(
            json.dumps(
                {
                    "state": "requested",
                    "identifier": value.identifier,
                    "statusLocation": value.status_location,
                    "means": value.means,
                },
                indent=2,
            )
        )
        return
    print(json.dumps(value, indent=2, sort_keys=True))


# --- commands ------------------------------------------------------------------------------------


def _sign_in(args: argparse.Namespace) -> int:
    with AccessForgeClient(args.base_url) as client:
        session = client.sign_in(args.email)
    _store_session(session)
    print(f"signed in as {args.email}; session stored at {SESSION_PATH} (mode 600)")
    return 0


def _project_list(args: argparse.Namespace) -> int:
    with _client(args) as client:
        _emit(client.call("list_projects", workspace_id=args.workspace))
    return 0


def _project_show(args: argparse.Namespace) -> int:
    with _client(args) as client:
        _emit(client.call("get_project", workspace_id=args.workspace, project_id=args.project))
    return 0


def _journey_list(args: argparse.Namespace) -> int:
    with _client(args) as client:
        _emit(
            client.call(
                "list_journey_versions", workspace_id=args.workspace, project_id=args.project
            )
        )
    return 0


def _run_request(args: argparse.Namespace) -> int:
    """Request a run. Prints "requested", because that is what happened.

    `--idempotency-key` is offered and not defaulted. Generating one automatically would make a
    retry safe by accident; the operator deciding to pass one is the operator deciding that two
    invocations mean one run.
    """
    with _client(args) as client:
        _emit(
            client.call(
                "request_run",
                workspace_id=args.workspace,
                body={"projectId": args.project, "manifestDigest": args.manifest},
                idempotency_key=args.idempotency_key,
            )
        )
    return 0


def _run_show(args: argparse.Namespace) -> int:
    with _client(args) as client:
        _emit(client.call("get_run", workspace_id=args.workspace, run_id=args.run))
    return 0


def _run_list(args: argparse.Namespace) -> int:
    with _client(args) as client:
        _emit(client.call("list_runs", workspace_id=args.workspace))
    return 0


def _run_cancel(args: argparse.Namespace) -> int:
    """Request cancellation. The response says requested, not stopped.

    A cancellation is a request to a machine that may be mid-action. The API reports it as requested
    and reports the stop separately when it is proven, and this prints exactly that rather than
    "cancelled".
    """
    with _client(args) as client:
        _emit(
            client.call(
                "request_cancellation",
                workspace_id=args.workspace,
                run_id=args.run,
                body={"reason": args.reason},
                if_match=args.if_match,
            )
        )
    return 0


def _runner_list(args: argparse.Namespace) -> int:
    with _client(args) as client:
        _emit(client.call("list_runners", workspace_id=args.workspace))
    return 0


def _export_verify(args: argparse.Namespace) -> int:
    """Verify a bundle offline, with no server involved at all.

    Delegated to `accessforge-verify` from module 17 rather than reimplemented. The point of that
    verifier is that a reader with the bundle and a key obtained out of band can check it without
    access to this system; a CLI that verified by calling the API would destroy that property while
    appearing to preserve it.
    """
    from accessforge_evidence.cli import main as verify

    argv = [args.bundle]
    if args.key:
        argv += ["--trust-root", args.key]
    return int(verify(argv))


def _operations(args: argparse.Namespace) -> int:
    """List what this build can call, from the generated table."""
    from ._operations import OPERATIONS

    for identifier, operation in sorted(OPERATIONS.items()):
        marker = "*" if operation.mutating else " "
        print(f"{marker} {identifier:<38} {operation.method:<7} {operation.path}")
    print("\n* changes state: needs a session, a CSRF header, and possibly If-Match.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="accessforge", description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("ACCESSFORGE_BASE_URL", DEFAULT_BASE_URL),
        help=f"the API to talk to (default {DEFAULT_BASE_URL})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    signin = sub.add_parser("sign-in", help="sign in and store a session")
    signin.add_argument("--email", required=True)
    signin.set_defaults(handler=_sign_in)

    project = sub.add_parser("project", help="projects").add_subparsers(
        dest="project_command", required=True
    )
    listing = project.add_parser("list")
    listing.add_argument("--workspace", required=True)
    listing.set_defaults(handler=_project_list)
    show = project.add_parser("show")
    show.add_argument("--workspace", required=True)
    show.add_argument("--project", required=True)
    show.set_defaults(handler=_project_show)

    journey = sub.add_parser("journey", help="journey versions").add_subparsers(
        dest="journey_command", required=True
    )
    journeys = journey.add_parser("list")
    journeys.add_argument("--workspace", required=True)
    journeys.add_argument("--project", required=True)
    journeys.set_defaults(handler=_journey_list)

    run = sub.add_parser("run", help="runs").add_subparsers(dest="run_command", required=True)
    request = run.add_parser("request", help="request a run (prints 'requested', not a result)")
    request.add_argument("--workspace", required=True)
    request.add_argument("--project", required=True)
    request.add_argument("--manifest", required=True, help="a sealed manifest digest")
    request.add_argument(
        "--idempotency-key",
        help="makes a retry of this exact request safe. Not generated for you: doing that would "
        "make retrying safe by accident rather than by decision.",
    )
    request.set_defaults(handler=_run_request)
    runs = run.add_parser("list")
    runs.add_argument("--workspace", required=True)
    runs.set_defaults(handler=_run_list)
    run_show = run.add_parser("show")
    run_show.add_argument("--workspace", required=True)
    run_show.add_argument("--run", required=True)
    run_show.set_defaults(handler=_run_show)
    cancel = run.add_parser("cancel", help="request cancellation (not the same as stopped)")
    cancel.add_argument("--workspace", required=True)
    cancel.add_argument("--run", required=True)
    cancel.add_argument("--reason", required=True)
    cancel.add_argument("--if-match", type=int, required=True, help="the revision you last read")
    cancel.set_defaults(handler=_run_cancel)

    runner = sub.add_parser("runner", help="desktop runners").add_subparsers(
        dest="runner_command", required=True
    )
    runners = runner.add_parser("list")
    runners.add_argument("--workspace", required=True)
    runners.set_defaults(handler=_runner_list)

    export = sub.add_parser("export", help="evidence bundles").add_subparsers(
        dest="export_command", required=True
    )
    verify = export.add_parser("verify", help="verify a bundle offline, with no server involved")
    verify.add_argument("bundle")
    verify.add_argument("--key", help="a trust root obtained independently of the bundle")
    verify.set_defaults(handler=_export_verify)

    operations = sub.add_parser("operations", help="list the routes this build can call")
    operations.set_defaults(handler=_operations)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except ApiProblem as problem:
        # The code, then the detail, then the request id. A caller scripting against this branches
        # on the exit status and reads the code; the request id is what connects what they saw to
        # what the server logged.
        print(f"{problem.code}: {problem.detail}", file=sys.stderr)
        if problem.request_id:
            print(f"request id: {problem.request_id}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
