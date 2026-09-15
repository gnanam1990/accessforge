"""Public read-only GitHub App probe and private approved-check transport composition.

This performs network operations only when invoked with temporary-token issuance enabled.
It narrows that token to one repository and read permissions, keeps it inside this function,
and requires successful token revocation before returning a metadata-only observation.
Operator JWT provisioning and durable workspace binding remain outside this module.
The public inspect_repository function cannot publish. The private create operation requires
a trusted service callback that reauthorizes and commits a new original publication reservation.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx

from accessforge_domain.canonical import digest
from accessforge_domain.timestamps import parse_rfc3339_utc, to_rfc3339_utc

from .github_webhooks import _constant, _object


class Refused(ValueError):
    """Static failure; never include credentials, raw response or HTTP exception details."""


@dataclass(frozen=True, slots=True)
class RepositoryScope:
    app_id: int
    installation_id: int
    account_id: int
    repository_id: int
    owner: str
    name: str

    def __post_init__(self) -> None:
        if any(
            type(v) is not int or not 1 <= v <= 2**63 - 1
            for v in (
                self.app_id,
                self.installation_id,
                self.account_id,
                self.repository_id,
            )
        ) or any(
            not isinstance(v, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", v)
            or v in {".", ".."}
            for v in (self.owner, self.name)
        ):
            raise Refused("repository scope malformed")


@dataclass(frozen=True, slots=True)
class CheckObservation:
    """Known-ID read of proposed payload fields, not creation provenance or uniqueness."""

    check_run_id: int
    payload_digest: str


@dataclass(frozen=True, slots=True)
class RepositoryAccess:
    scope: RepositoryScope
    observed_at: str
    token_revoked: bool
    meaning: str = "POINT_IN_TIME_READ_ACCESS_NOT_PUBLICATION_AUTHORITY"
    commit_sha: str | None = None
    check_observation: CheckObservation | None = None


@dataclass(frozen=True, slots=True)
class CreatedCheck:
    intent_id: str
    check_run_id: int
    payload_digest: str
    observed_at: str
    token_revoked: bool = True


@dataclass(frozen=True, slots=True)
class _CreateCheck:
    """Trusted service composition only, never populated from an API request body."""

    body: dict[str, Any]
    # Must revalidate retained evidence/current authority and commit a NEW durable create slot.
    authorize: Callable[[], str]


def inspect_repository(
    scope: RepositoryScope,
    *,
    app_jwt: str,
    allow_temporary_token_issuance: bool = False,
    commit_sha: str | None = None,
    check_run_id: int | None = None,
    _transport: httpx.BaseTransport | None = None,
) -> RepositoryAccess:
    """Inspect current repository/check metadata without any check-write capability."""
    result = _repository_operation(
        scope,
        app_jwt=app_jwt,
        allow_temporary_token_issuance=allow_temporary_token_issuance,
        commit_sha=commit_sha,
        check_run_id=check_run_id,
        _transport=_transport,
    )
    assert isinstance(result, RepositoryAccess)
    return result


def _repository_operation(
    scope: RepositoryScope,
    *,
    app_jwt: str,
    allow_temporary_token_issuance: bool = False,
    commit_sha: str | None = None,
    check_run_id: int | None = None,
    _transport: httpx.BaseTransport | None = None,
    create: _CreateCheck | None = None,
) -> RepositoryAccess | CreatedCheck:
    """Check current App/installation/account/repository identity, without returning a token.

    `_transport` is a trusted test seam, never request data. Production fixes HTTPS GitHub.com,
    refuses redirects/compression and ignores proxy/credential environment discovery. No retry
    occurs, including uncertain token creation or revocation. Failure is unconfirmed, not proof
    that a remotely created token never existed; its GitHub expiry remains the fallback bound.
    """
    if allow_temporary_token_issuance is not True:
        raise Refused("temporary installation-token issuance requires explicit authorization")
    if create is not None:
        if commit_sha is None or check_run_id is not None or not callable(create.authorize):
            raise Refused("one original create operation required")
        create = _CreateCheck(deepcopy(create.body), create.authorize)
        if (
            create.body.get("head_sha") != commit_sha
            or not set(create.body)
            <= {"name", "head_sha", "external_id", "status", "conclusion", "output"}
            or len(json.dumps(create.body).encode()) > 65536
        ):
            raise Refused("bounded original check payload required")
    if commit_sha is not None and (
        not isinstance(commit_sha, str)
        or not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", commit_sha)
    ):
        raise Refused("an exact immutable source commit is required")
    if check_run_id is not None and (
        type(check_run_id) is not int or not 1 <= check_run_id <= 2**63 - 1 or commit_sha is None
    ):
        raise Refused("check inspection requires an exact check ID and source commit")
    if (
        not isinstance(scope, RepositoryScope)
        or not isinstance(app_jwt, str)
        or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,8192}\.[A-Za-z0-9_-]{1,8192}\.[A-Za-z0-9_-]{1,8192}",
            app_jwt,
        )
    ):
        raise Refused("App authentication unavailable")
    deadline = time.monotonic() + 30
    token: str | None = None
    permissions = {"metadata": "read", "contents": "read"}
    if check_run_id is not None:
        permissions["checks"] = "read"
    if create is not None:
        permissions["checks"] = "write"
    check_observation: CheckObservation | None = None
    created: CreatedCheck | None = None
    with httpx.Client(
        transport=_transport, trust_env=False, follow_redirects=False, timeout=5
    ) as client:

        def request(
            method: str,
            path: str,
            credential: str,
            status: int,
            body: dict[str, Any] | None = None,
            *,
            cleanup: bool = False,
        ) -> dict[str, Any]:
            if not cleanup and time.monotonic() >= deadline:
                raise Refused("GitHub access inspection deadline elapsed")
            try:
                with client.stream(
                    method,
                    "https://api.github.com" + path,
                    headers={
                        "Authorization": "Bearer " + credential,
                        "Accept": "application/vnd.github+json",
                        "Accept-Encoding": "identity",
                        "X-GitHub-Api-Version": "2026-03-10",
                    },
                    json=body,
                ) as response:
                    if (
                        response.status_code != status
                        or response.headers.get("content-encoding", "identity") != "identity"
                    ):
                        raise Refused("GitHub access response unavailable")
                    if status == 204:
                        return {}
                    raw = bytearray()
                    for chunk in response.iter_raw():
                        if len(raw) + len(chunk) > 1_048_576 or time.monotonic() >= deadline:
                            raise Refused("GitHub access response exceeds limits")
                        raw.extend(chunk)
                    value = json.loads(
                        bytes(raw).decode("utf-8"),
                        object_pairs_hook=_object,
                        parse_constant=_constant,
                    )
                    if not isinstance(value, dict):
                        raise Refused("GitHub access response malformed")
                    return value
            except (httpx.HTTPError, ValueError, UnicodeError, RecursionError):
                raise Refused("GitHub access inspection unconfirmed") from None

        def installation() -> None:
            observed = request("GET", f"/app/installations/{scope.installation_id}", app_jwt, 200)
            account = observed.get("account")
            granted = observed.get("permissions")
            if (
                type(observed.get("id")) is not int
                or observed["id"] != scope.installation_id
                or type(observed.get("app_id")) is not int
                or observed["app_id"] != scope.app_id
                or "suspended_at" not in observed
                or observed["suspended_at"] is not None
                or not isinstance(account, dict)
                or type(account.get("id")) is not int
                or account["id"] != scope.account_id
                or not isinstance(granted, dict)
                or any(
                    not isinstance(granted.get(key), str) or granted[key] not in {"read", "write"}
                    for key in permissions
                )
                or (create is not None and granted.get("checks") != "write")
            ):
                raise Refused("current installation scope or read permissions differ")

        installation()
        try:
            issued = request(
                "POST",
                f"/app/installations/{scope.installation_id}/access_tokens",
                app_jwt,
                201,
                {"repository_ids": [scope.repository_id], "permissions": permissions},
            )
            value = issued.get("token")
            # Installation tokens are opaque bearer credentials, including GitHub's stateless
            # ghs_APPID_JWT format. Do not assume a legacy length or alphanumeric-only body.
            # Bound the header and forbid whitespace/control injection; remote identity and
            # permissions are still checked independently below, never decoded from the token.
            if (
                not isinstance(value, str)
                or not 20 <= len(value) <= 8192
                or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", value)
            ):
                raise Refused("temporary installation credential unavailable")
            token = value  # Own cleanup before checking the rest of the issuance response.
            if issued.get("permissions") != permissions:
                raise Refused("temporary credential permissions were not narrowed")
            expiry = issued.get("expires_at")
            if not isinstance(expiry, str) or len(expiry) > 40:
                raise Refused("temporary credential expiry unavailable")
            try:
                expires = parse_rfc3339_utc(expiry)
            except ValueError:
                raise Refused("temporary credential expiry unavailable") from None
            now = datetime.now(UTC)
            if not now + timedelta(seconds=30) < expires <= now + timedelta(hours=1, seconds=60):
                raise Refused("temporary credential expiry unavailable")
            repository = request("GET", f"/repos/{scope.owner}/{scope.name}", token, 200)
            owner = repository.get("owner")
            if (
                type(repository.get("id")) is not int
                or repository["id"] != scope.repository_id
                or repository.get("full_name") != f"{scope.owner}/{scope.name}"
                or not isinstance(owner, dict)
                or type(owner.get("id")) is not int
                or owner["id"] != scope.account_id
            ):
                raise Refused("repository identity or ownership differs")
            if commit_sha is not None:
                commit = request(
                    "GET",
                    f"/repos/{scope.owner}/{scope.name}/git/commits/{commit_sha}",
                    token,
                    200,
                )
                if commit.get("sha") != commit_sha:
                    raise Refused("repository commit identity differs")
                if check_run_id is not None:
                    observed_check = request(
                        "GET",
                        f"/repos/{scope.owner}/{scope.name}/check-runs/{check_run_id}",
                        token,
                        200,
                    )
                    check_observation = _observe_check(
                        observed_check,
                        scope=scope,
                        commit_sha=commit_sha,
                        check_run_id=check_run_id,
                    )
                # A rename/transfer during the commit read must not silently retain old scope.
                fresh_repository = request("GET", f"/repos/{scope.owner}/{scope.name}", token, 200)
                fresh_owner = fresh_repository.get("owner")
                if (
                    type(fresh_repository.get("id")) is not int
                    or fresh_repository["id"] != scope.repository_id
                    or fresh_repository.get("full_name") != f"{scope.owner}/{scope.name}"
                    or not isinstance(fresh_owner, dict)
                    or type(fresh_owner.get("id")) is not int
                    or fresh_owner["id"] != scope.account_id
                ):
                    raise Refused("repository identity changed during commit inspection")
            installation()  # Detect suspension/removal/permission change during the probe.
            if datetime.now(UTC) >= expires:
                raise Refused("temporary credential expired during inspection")
            if create is not None:
                # All remote identity checks precede the final local authorization/unique
                # reservation. The callback cannot reuse an existing intent as dispatch authority.
                intent_id = create.authorize()
                if str(UUID(intent_id)) != intent_id:
                    raise Refused("original publication reservation unavailable")
                if datetime.now(UTC) >= expires:
                    raise Refused("temporary credential expired before publication")
                value = request(
                    "POST",
                    f"/repos/{scope.owner}/{scope.name}/check-runs",
                    token,
                    201,
                    create.body,
                )
                identifier = value.get("id")
                if type(identifier) is not int or not 1 <= identifier <= 2**63 - 1:
                    raise Refused("check creation response unconfirmed")
                observation = _observe_check(
                    value,
                    scope=scope,
                    commit_sha=commit_sha or "",
                    check_run_id=identifier,
                )
                if observation.payload_digest != digest(create.body):
                    raise Refused("created check differs from its approved payload")
                created = CreatedCheck(
                    intent_id,
                    identifier,
                    observation.payload_digest,
                    to_rfc3339_utc(datetime.now(UTC)),
                )
        finally:
            if token is not None:
                request("DELETE", "/installation/token", token, 204, cleanup=True)
    if created is not None:
        return created  # Only after successful token revocation; failures remain unconfirmed.
    return RepositoryAccess(
        scope,
        to_rfc3339_utc(datetime.now(UTC)),
        True,
        commit_sha=commit_sha,
        check_observation=check_observation,
    )


def _observe_check(
    value: dict[str, Any], *, scope: RepositoryScope, commit_sha: str, check_run_id: int
) -> CheckObservation:
    """Hash only the create-preview fields; never expose untrusted check text or URLs.

    Caller must compare this digest with the exact stored request body before interpreting a
    match. This does not prove the check was created by our intent, that no duplicate exists,
    or that mutable GitHub-owned metadata/URLs remain unchanged.
    """
    app, output = value.get("app"), value.get("output")
    if (
        type(value.get("id")) is not int
        or value["id"] != check_run_id
        or not isinstance(app, dict)
        or type(app.get("id")) is not int
        or app["id"] != scope.app_id
        or value.get("head_sha") != commit_sha
        or value.get("name") != "AccessForge journey evidence"
        or not isinstance(value.get("external_id"), str)
        or not re.fullmatch(r"accessforge:[a-f0-9]{64}", value["external_id"])
        or value.get("status") not in ("queued", "in_progress", "completed")
        or not isinstance(output, dict)
        or not isinstance(output.get("title"), str)
        or not isinstance(output.get("summary"), str)
        or output.get("text") not in (None, "")
        or type(output.get("annotations_count")) is not int
        or output["annotations_count"] != 0
    ):
        raise Refused("check identity or payload unavailable")
    conclusion = value.get("conclusion")
    if (
        value["status"] == "completed"
        and conclusion
        not in (
            "success",
            "failure",
            "neutral",
            "cancelled",
            "timed_out",
            "action_required",
            "skipped",
            "stale",
            "startup_failure",
        )
    ) or (value["status"] != "completed" and conclusion is not None):
        raise Refused("check lifecycle unavailable")
    body = {key: value[key] for key in ("name", "head_sha", "external_id", "status")}
    body["output"] = {"title": output["title"], "summary": output["summary"]}
    if conclusion is not None:
        body["conclusion"] = conclusion
    return CheckObservation(check_run_id, digest(body))
