"""Origin policy for automated effects.

An origin is the answer to "where may this run actually act". Getting it wrong is how a test fixture
becomes a production incident, so the rules here are deliberately narrow and refuse rather than
normalize away anything ambiguous.

Three ideas do the work:

* **Normalization before comparison.** ``HTTP://Example.test:80/app/`` and
  ``http://example.test/app`` are the same origin; a string comparison would treat them as
  different and an attacker-chosen casing or default port would slip past an allowlist.
* **An origin is scheme + host + port.** Path is *not* part of it, so an allowlist entry cannot be
  narrowed to a path and then widened by navigating elsewhere on the same host.
* **Redirects are revalidated, not followed.** Authority is granted to a destination, not to
  whatever that destination later points at. A redirect to an unapproved origin is refused even when
  the original request was approved — that is the whole mechanism by which a staging URL becomes a
  production one.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_PORTS = {"http": 80, "https": 443}

# Addresses that are never a legitimate automation target. Link-local and cloud metadata endpoints
# are the classic SSRF destinations, and a "local and staging are allowed" policy must not quietly
# include them.
_FORBIDDEN_HOSTS = frozenset(
    {
        "169.254.169.254",  # cloud instance metadata
        "metadata.google.internal",
        "metadata",
    }
)


class OriginError(ValueError):
    """A URL could not be accepted as an automation target."""


@dataclass(frozen=True, slots=True)
class Origin:
    """A normalized scheme/host/port triple."""

    scheme: str
    host: str
    port: int

    def __str__(self) -> str:
        if DEFAULT_PORTS.get(self.scheme) == self.port:
            return f"{self.scheme}://{self.host}"
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def is_loopback(self) -> bool:
        if self.host == "localhost":
            return True
        try:
            return ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            return False

    @property
    def is_private(self) -> bool:
        """Whether the host is a private or link-local address.

        Local and staging addresses may legitimately be private, so this is information for a
        per-environment policy rather than a blanket verdict. SECURITY-PRIVACY is explicit that
        "allow all internal networks" is not an acceptable rule.
        """
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError:
            return False
        return address.is_private or address.is_link_local


def normalize_origin(url: str) -> Origin:
    """Parse a URL down to its origin, or refuse.

    Refusals are specific because each one corresponds to a way authority could be widened:
    a missing scheme leaves the transport open, a userinfo component can disguise the real host,
    and a non-http scheme is not something a browser journey can drive.
    """
    if not isinstance(url, str) or not url.strip():
        raise OriginError("an origin requires a URL")

    parts = urlsplit(url.strip())

    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise OriginError(
            f"scheme {parts.scheme!r} is not an automatable origin; only http and https are"
        )

    if parts.username or parts.password:
        # user:pass@host is how a host gets visually disguised; refuse rather than strip.
        raise OriginError("a URL with embedded credentials is not an acceptable origin")

    host = (parts.hostname or "").lower()
    if not host:
        raise OriginError(f"no host in {url!r}")
    if host in _FORBIDDEN_HOSTS:
        raise OriginError(f"host {host!r} is never a permitted automation target")

    try:
        port = parts.port or DEFAULT_PORTS[scheme]
    except ValueError as exc:  # malformed port
        raise OriginError(f"invalid port in {url!r}") from exc

    return Origin(scheme=scheme, host=host, port=port)


def same_origin(left: str, right: str) -> bool:
    """Whether two URLs share an origin, compared after normalization."""
    return normalize_origin(left) == normalize_origin(right)


def assert_permitted(url: str, *, allowed: frozenset[Origin]) -> Origin:
    """Raise unless ``url``'s origin is explicitly allowed.

    Membership is exact. There is no subdomain wildcard and no "parent domain implies child",
    because an approved ``staging.example.test`` must not carry any authority over
    ``example.test`` or over a sibling a third party controls.
    """
    origin = normalize_origin(url)
    if origin not in allowed:
        raise OriginError(
            f"origin {origin} is not in this environment's allowlist "
            f"({', '.join(sorted(str(o) for o in allowed)) or 'empty'})"
        )
    return origin


def assert_redirect_permitted(*, from_url: str, to_url: str, allowed: frozenset[Origin]) -> Origin:
    """Revalidate a redirect target against the allowlist.

    Authority belongs to a destination, not to whatever that destination later points at. This is
    the specific mechanism by which an approved staging URL becomes a production one, so the target
    is checked on its own merits and the fact that the original request was approved buys nothing.
    """
    normalize_origin(from_url)  # the source must itself be well formed
    try:
        return assert_permitted(to_url, allowed=allowed)
    except OriginError as exc:
        raise OriginError(
            f"redirect from {from_url} to {to_url} leaves the allowlist; a redirect cannot "
            f"broaden authority ({exc})"
        ) from exc
