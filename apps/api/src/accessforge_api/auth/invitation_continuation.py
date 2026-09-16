"""An invitation reference carried across login, never identity or membership authority."""

from dataclasses import dataclass
from uuid import UUID

from .github_identity import GitHubIdentityError


@dataclass(frozen=True, slots=True)
class InvitationContinuation:
    workspace_id: str
    invitation_id: str

    def __post_init__(self) -> None:
        try:
            if any(str(UUID(value)) != value for value in (self.workspace_id, self.invitation_id)):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise GitHubIdentityError("GitHub invitation continuation refused") from None

    @property
    def cookie_suffix(self) -> str:
        return f".{self.workspace_id}.{self.invitation_id}"

    @property
    def return_path(self) -> str:
        # Both values are canonical UUIDs, never a caller-controlled redirect URL.
        return (
            f"/workspaces?invitationWorkspace={self.workspace_id}&invitationId={self.invitation_id}"
        )
