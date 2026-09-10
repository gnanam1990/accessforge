"""The `/v1` surface.

One rule shapes every route in this package: **a route handler contains no domain logic.** It
resolves authority, validates shape, calls a domain or persistence function, and serialises the
result. Where a state transition happens it goes through the reducers, because a route that
reimplemented a transition would be a second definition of the lifecycle — and the second one is
always the one that drifts.

The consequence is that these files are thin and slightly repetitive, which is the intended trade.
The repetition is in the plumbing; the decisions all live in one place each.
"""

from .exports import router as exports_router
from .findings import router as findings_router
from .grants import router as grants_router
from .journeys import router as journeys_router
from .projects import router as projects_router
from .runners import router as runners_router
from .runs import router as runs_router
from .schedules import router as schedules_router
from .session import router as session_router
from .settings import router as settings_router
from .stream import router as stream_router

__all__ = [
    "exports_router",
    "findings_router",
    "grants_router",
    "journeys_router",
    "projects_router",
    "runners_router",
    "runs_router",
    "schedules_router",
    "settings_router",
    "session_router",
    "stream_router",
]
