"""Explicit host-operator GitHub account binding; never run during startup.

Verify the person's numeric GitHub ID independently before binding an existing
local user UUID. This command does not verify account ownership for you. Requires
operator database access through ACCESSFORGE_DATABASE_URL; no migration or OAuth
call is performed. Revocation is permanent; rebind/restore is not supported.
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg

from accessforge_api.auth.github_accounts import bind_account, revoke_binding
from accessforge_api.auth.github_identity import GitHubIdentityError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("bind", "revoke"))
    parser.add_argument("--github-user-id", required=True)
    parser.add_argument("--user-id", help="existing local user UUID; required only for bind")
    parser.add_argument("--operator", required=True, help="audit label, not an authorization token")
    args = parser.parse_args(argv)
    database_url = os.environ.get("ACCESSFORGE_DATABASE_URL")
    if not database_url or (args.action == "bind") != (args.user_id is not None):
        print("database configuration or command arguments missing/invalid", file=sys.stderr)
        return 2
    try:
        if args.action == "bind":
            bind_account(
                database_url,
                github_subject=args.github_user_id,
                user_id=args.user_id,
                operator=args.operator,
            )
        else:
            revoke_binding(database_url, github_subject=args.github_user_id, operator=args.operator)
    except (GitHubIdentityError, psycopg.Error):
        # Database errors can contain DSNs, query parameters or account metadata.
        print(
            "identity operation refused or unconfirmed; inspect state before retrying",
            file=sys.stderr,
        )
        return 1
    print("identity operation committed; no workspace membership changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
