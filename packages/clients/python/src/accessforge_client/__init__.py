"""The Python client for the AccessForge API.

Two halves, and the split is deliberate. `_operations.py` is generated from the published contract,
which is generated from the live application — so a route that changes shape changes here on the
same commit. `client.py` is written by hand, because how to talk to this API *safely* is not
something a contract describes: the CSRF pairing, when `If-Match` is mandatory, what an
`Idempotency-Key` means for a retry, and why a 202 is not a completion.

The client refuses to paper over the things the API is careful about. `202 Accepted` comes back as
`Requested`, never as a result. A problem document becomes a typed exception carrying the machine
code, never a string somebody will match on. And nothing here retries a mutation on its own.
"""

from ._operations import OPERATIONS, PATHS, Operation
from .client import (
    AccessForgeClient,
    ApiProblem,
    NotAuthenticated,
    Requested,
    StaleRevision,
)

__all__ = [
    "OPERATIONS",
    "PATHS",
    "AccessForgeClient",
    "ApiProblem",
    "NotAuthenticated",
    "Operation",
    "Requested",
    "StaleRevision",
]
