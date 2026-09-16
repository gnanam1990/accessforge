"""An invitation reference carried across login, never identity or membership authority."""

import base64
import re
from dataclasses import dataclass, field
from uuid import UUID

from .github_identity import GitHubIdentityError


@dataclass(frozen=True, slots=True)
class InvitationContinuation:
    workspace_id: str
    invitation_id: str
    contact_email: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        try:
            if any(str(UUID(value)) != value for value in (self.workspace_id, self.invitation_id)):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise GitHubIdentityError("GitHub invitation continuation refused") from None
        if self.contact_email is not None and (
            not isinstance(self.contact_email, str)
            or len(self.contact_email) > 254
            or not re.fullmatch(r"[^\s@\x00-\x1f\x7f]+@[^\s@\x00-\x1f\x7f]+", self.contact_email)
        ):
            raise GitHubIdentityError("GitHub invitation continuation refused")

    @property
    def cookie_suffix(self) -> str:
        suffix = f".{self.workspace_id}.{self.invitation_id}"
        if self.contact_email is not None:
            suffix += "." + base64.urlsafe_b64encode(self.contact_email.encode()).decode().rstrip(
                "="
            )
        return suffix

    @property
    def return_path(self) -> str:
        # Both values are canonical UUIDs, never a caller-controlled redirect URL.
        return (
            f"/workspaces?invitationWorkspace={self.workspace_id}&invitationId={self.invitation_id}"
        )
