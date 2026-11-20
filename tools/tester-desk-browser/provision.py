"""Provision a disposable tester-desk scene through DB setup and the live API.

Used by the browser check (`check.mjs`): creates a fresh workspace, owner,
reviewer, finance member, a client viewer, an enrolled tester and an outsider,
then drives the agency's side of commissioning through the real HTTP entry
points (sign-in, consent terms, client, commission + brief, publish, tester
enrollment, client-viewer grant). Everything is namespaced by a run id so the
data stays disposable; nothing outside this workspace is touched.

Prints a JSON record the browser check consumes:
{workspaceId, commissionId, reference, emails: {...}, userIds: {...}}

Usage:
  uv run python tools/tester-desk-browser/provision.py \
    --database-url postgresql://af_td:af_td_local@localhost:5432/af_td_dev \
    --api-base http://127.0.0.1:8123
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

import httpx
import psycopg

sys.path.insert(0, "apps/api/src")

from accessforge_api.auth.workspace_setup import provision_workspace  # noqa: E402
from accessforge_persistence import migrate, workspace_connection  # noqa: E402

# The AF-21 reference scene, kept in step with tests/integration/conftest.py.
AF21_BRIEF = {
    "releaseLabel": "r18",
    "releaseDigest": "a" * 64,
    "targetUrl": "https://checkout.example.test/r18",
    "accessNote": "demo account tester@checkout.example.test / passphrase desk-demo",
    "taskSteps": [
        {"stepId": "s1", "title": "Open the release checkout URL"},
        {"stepId": "s2", "title": "Fill the contact form"},
        {"stepId": "s3", "title": "Fill the shipping form"},
        {"stepId": "s4", "title": "Advance past the shipping form"},
        {"stepId": "s5", "title": "Complete payment"},
    ],
    "environment": {
        "browserName": "Safari",
        "browserVersion": "18",
        "assistiveTechnology": "VoiceOver",
        "inputMode": "KEYBOARD_ONLY",
    },
    "rubric": {"requiredEvidenceKinds": ["ACTION_TRACE"]},
    "permittedEvidence": ["ACTION_TRACE", "SPEECH_TRANSCRIPT", "SCREENSHOT", "NOTE"],
    "feeMinor": 2000,
    "currency": "USD",
    "consentTermsVersion": 1,
}

CONSENT_BODY = (
    "You will perform the stated journey yourself with the declared assistive "
    "technology. You choose which permitted artifacts to attach. You may withdraw "
    "consent at any time; retained evidence follows the workspace data policy."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--api-base")
    parser.add_argument(
        "--migrate-only",
        action="store_true",
        help="apply migrations to --database-url and exit (the check runs this first)",
    )
    args = parser.parse_args(argv)

    # The check must work against a fresh, empty database too: migrate first.
    # migrate() is idempotent, so pointing at an already-current database is fine.
    migrate(args.database_url)
    if args.migrate_only:
        return 0
    if not args.api_base:
        parser.error("--api-base is required unless --migrate-only")

    run = uuid.uuid4().hex[:8]
    workspace = str(uuid.uuid4())
    ids = {
        k: str(uuid.uuid4())
        for k in ("owner", "reviewer", "finance", "tester", "outsider", "client")
    }
    emails = {
        "owner": f"td-browser-owner-{run}@agency.example.test",
        "reviewer": f"td-browser-reviewer-{run}@agency.example.test",
        "finance": f"td-browser-finance-{run}@agency.example.test",
        "tester": f"td-browser-tester-{run}@testers.example.test",
        "outsider": f"td-browser-outsider-{run}@elsewhere.example.test",
        "client": f"td-browser-viewer-{run}@client.example.test",
    }

    # Owner + workspace come from the audited provisioning path, not a raw insert.
    provision_workspace(
        args.database_url,
        user_id=ids["owner"],
        workspace_id=workspace,
        email=emails["owner"],
        name="Tester Desk Browser Check",
        operator="tester-desk-browser-check",
    )

    # Remaining synthetic people and memberships mirror tests/integration/conftest.py:
    # the tester and outsider deliberately carry no membership row.
    with psycopg.connect(args.database_url) as conn:
        for key in ("reviewer", "finance", "tester", "outsider", "client"):
            conn.execute(
                "INSERT INTO app_user(id,email) VALUES (%s,%s)",
                (ids[key], emails[key]),
            )
        conn.commit()

    with workspace_connection(args.database_url, workspace) as conn:
        for key, role in (("reviewer", "REVIEWER"), ("finance", "OWNER")):
            conn.execute(
                "INSERT INTO workspace_membership(workspace_id,user_id,role) VALUES (%s,%s,%s)",
                (workspace, ids[key], role),
            )

    # Agency side of the commission goes through the real HTTP surface.
    # trust_env=False: a stray ALL_PROXY/SOCKS proxy in the caller's environment
    # must not sit between this script and a loopback API.
    agency = httpx.Client(base_url=args.api_base, timeout=15.0, trust_env=False)
    response = agency.post("/v1/sessions", json={"email": emails["owner"]})
    if response.status_code != 201:
        raise SystemExit(f"owner sign-in refused: {response.status_code} {response.text[:200]}")
    csrf = agency.cookies.get("accessforge_csrf")
    agency.headers["x-csrf-token"] = csrf

    def post(path: str, body: dict, want: int) -> dict:
        res = agency.post(path, json=body)
        if res.status_code != want:
            raise SystemExit(f"POST {path} -> {res.status_code} {res.text[:300]}")
        return res.json()

    base = f"/v1/workspaces/{workspace}/tester-desk"
    terms = post(f"{base}/consent-terms", {"body": CONSENT_BODY}, 201)
    client = post(f"{base}/clients", {"name": "Client Co"}, 201)
    post(f"{base}/clients/{client['clientId']}/viewers", {"userId": ids["client"]}, 201)
    commission = post(
        f"{base}/commissions",
        {"clientId": client["clientId"], "reference": "AF-21", "brief": AF21_BRIEF},
        201,
    )
    post(f"{base}/commissions/{commission['commissionId']}/publish", {}, 200)
    tester = post(
        f"{base}/testers",
        {
            "userId": ids["tester"],
            "displayName": "Taylor Tester",
            "capabilities": {
                "browserName": "Safari",
                "browserVersion": "18",
                "assistiveTechnology": "VoiceOver",
            },
            "payoutReference": "local-ledger:taylor",
        },
        201,
    )

    print(
        json.dumps(
            {
                "runId": run,
                "workspaceId": workspace,
                "commissionId": commission["commissionId"],
                "testerId": tester["testerId"],
                "clientId": client["clientId"],
                "consentTermsVersion": terms["version"],
                "reference": "AF-21",
                "emails": emails,
                "userIds": ids,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
