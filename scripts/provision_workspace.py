"""Provision a NEW local user and workspace with audited OWNER membership.

Requires explicit operator database access and authorization for the named owner.
Retain the supplied UUIDs before invoking. Never retry an unknown commit outcome
without inspecting those records. Does not bind GitHub, log in, or run migrations.
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg

from accessforge_api.auth.workspace_setup import WorkspaceSetupRefused, provision_workspace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("user-id", "workspace-id", "email", "name", "operator"):
        parser.add_argument("--" + field, required=True)
    parser.add_argument("--confirm-create", action="store_true")
    args = parser.parse_args(argv)
    database_url = os.environ.get("ACCESSFORGE_DATABASE_URL")
    if not database_url or not args.confirm_create:
        print("database configuration and --confirm-create are required", file=sys.stderr)
        return 2
    try:
        provision_workspace(
            database_url,
            user_id=args.user_id,
            workspace_id=args.workspace_id,
            email=args.email,
            name=args.name,
            operator=args.operator,
        )
    except (WorkspaceSetupRefused, psycopg.Error):
        print("setup refused or unconfirmed; inspect supplied IDs before retrying", file=sys.stderr)
        return 1
    print("user and workspace committed; identity binding and login remain separate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
