"""Explicit trusted-host connection; never creates or approves a GitHub check."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from accessforge_domain.authorization import HumanPrincipal, Role
from accessforge_persistence import workspace_connection

from .github_access import RepositoryScope
from .github_app_jwt import sign_app_jwt
from .github_connections import _authorize, connect_repository
from .github_operator import _private_bytes, _unique


def _scope(path: Path) -> tuple[HumanPrincipal, RepositoryScope]:
    value: Any = json.loads(_private_bytes(path, 8192).decode("utf-8"), object_pairs_hook=_unique)
    identities = {"workspaceId", "userId", "sessionId"}
    if (
        not isinstance(value, dict)
        or set(value)
        != identities
        | {
            "schemaVersion",
            "appId",
            "installationId",
            "accountId",
            "repositoryId",
            "owner",
            "repository",
        }
        or type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != 1
    ):
        raise ValueError("connection scope unavailable")
    for key in identities:
        if not isinstance(value[key], str) or str(UUID(value[key])) != value[key]:
            raise ValueError("canonical identity required")
    return (
        HumanPrincipal(value["userId"], value["workspaceId"], Role.OWNER, value["sessionId"]),
        RepositoryScope(
            value["appId"],
            value["installationId"],
            value["accountId"],
            value["repositoryId"],
            value["owner"],
            value["repository"],
        ),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Connect one exact operator-configured GitHub repository"
    )
    parser.add_argument("--scope-file", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--allow-installation-token-issuance", action="store_true")
    args = parser.parse_args(argv)
    if not args.allow_installation_token_issuance:
        parser.error(
            "--allow-installation-token-issuance required; no file read or remote work started"
        )
    entered = False
    try:
        principal, scope = _scope(args.scope_file)
        database_url = os.environ["ACCESSFORGE_DATABASE_URL"]
        with workspace_connection(database_url, principal.workspace_id) as conn:
            conn.execute("SET LOCAL statement_timeout='5s'")
            _authorize(conn, principal)
        jwt = sign_app_jwt(app_id=scope.app_id, private_pem=_private_bytes(args.key_file, 16384))
        entered = True
        binding_id = connect_repository(
            database_url,
            principal=principal,
            scope=scope,
            app_jwt=jwt.bearer,
            allow_temporary_token_issuance=True,
        )
    except (Exception, KeyboardInterrupt):
        if not entered:
            parser.exit(2, "GITHUB_CONNECTION_REFUSED; connection service was not invoked.\n")
        parser.exit(
            1,
            "GITHUB_CONNECTION_UNCONFIRMED; inspect original workspace bindings "
            "and installation credential before another attempt.\n",
        )
    print(f"GITHUB_BINDING_RETAINED binding={binding_id}; publication is not approved")


if __name__ == "__main__":
    main()
