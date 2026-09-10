"""Origin normalization and policy.

An origin decides where a run may act, so every case here corresponds to a way authority could be
widened: casing, default ports, userinfo, paths, subdomains and redirects.

Requirements: FR-001, FR-002. Invariants: INV-07, INV-08.
"""

from __future__ import annotations

import pytest

from accessforge_domain.origins import (
    Origin,
    OriginError,
    assert_permitted,
    assert_redirect_permitted,
    normalize_origin,
    same_origin,
)

STAGING = normalize_origin("https://staging.example.test")
LOCAL = normalize_origin("http://127.0.0.1:8081")
ALLOWED = frozenset({STAGING, LOCAL})


# --- normalization --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("http://example.test", "http://example.test"),
        ("HTTP://Example.TEST", "http://example.test"),
        ("http://example.test:80", "http://example.test"),
        ("http://example.test:80/app/page?q=1#frag", "http://example.test"),
        ("https://example.test:443", "https://example.test"),
        ("https://example.test:8443", "https://example.test:8443"),
        ("http://127.0.0.1:8081/form/abc", "http://127.0.0.1:8081"),
    ],
)
def test_equivalent_urls_normalize_to_one_origin(written: str, expected: str) -> None:
    assert str(normalize_origin(written)) == expected


def test_path_is_not_part_of_the_origin() -> None:
    """An allowlist entry cannot be narrowed to a path and then widened by navigating elsewhere."""
    assert same_origin("http://example.test/a", "http://example.test/b/c?d=e")


def test_case_and_default_port_cannot_evade_an_allowlist() -> None:
    assert_permitted("HTTPS://Staging.Example.TEST:443/login", allowed=ALLOWED)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "example.test",  # no scheme: the transport would be unconstrained
        "ftp://example.test",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,<script>",
        "http://",  # no host
    ],
)
def test_unusable_urls_are_refused(url: str) -> None:
    with pytest.raises(OriginError):
        normalize_origin(url)


def test_embedded_credentials_are_refused_rather_than_stripped() -> None:
    """`user:pass@host` is how a host gets visually disguised; silently stripping would hide it."""
    with pytest.raises(OriginError, match="embedded credentials"):
        normalize_origin("https://staging.example.test@evil.test/")


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/",
        "http://metadata/",
    ],
)
def test_metadata_endpoints_are_never_permitted(url: str) -> None:
    """The classic SSRF destination. A "local and private is fine" policy must not include it."""
    with pytest.raises(OriginError, match="never a permitted"):
        normalize_origin(url)


# --- allowlist membership ----------------------------------------------------------------------


def test_a_listed_origin_is_permitted() -> None:
    # Allowed-path control.
    assert assert_permitted("https://staging.example.test/login", allowed=ALLOWED) == STAGING
    assert assert_permitted("http://127.0.0.1:8081/form/x", allowed=ALLOWED) == LOCAL


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test",  # the parent domain
        "https://evil.staging.example.test",  # a child
        "https://staging.example.test.evil.test",  # a suffix trick
        "http://staging.example.test",  # different scheme
        "https://staging.example.test:8443",  # different port
        "http://127.0.0.1:9999",  # different port on the same host
        "http://localhost:8081",  # a different host spelling is a different origin
    ],
)
def test_neighbouring_origins_are_not_permitted(url: str) -> None:
    """Membership is exact: no wildcards, no parent-implies-child, no suffix matching."""
    with pytest.raises(OriginError, match="not in this environment's allowlist"):
        assert_permitted(url, allowed=ALLOWED)


def test_an_empty_allowlist_permits_nothing() -> None:
    with pytest.raises(OriginError):
        assert_permitted("https://staging.example.test", allowed=frozenset())


# --- redirects ---------------------------------------------------------------------------------


def test_a_redirect_within_the_allowlist_is_permitted() -> None:
    # Allowed-path control.
    assert (
        assert_redirect_permitted(
            from_url="https://staging.example.test/login",
            to_url="https://staging.example.test/dashboard",
            allowed=ALLOWED,
        )
        == STAGING
    )


def test_a_redirect_to_production_is_refused() -> None:
    """The specific mechanism by which an approved staging URL becomes a production one.

    Authority belongs to a destination, not to whatever that destination later points at.
    """
    with pytest.raises(OriginError, match="cannot broaden authority"):
        assert_redirect_permitted(
            from_url="https://staging.example.test/login",
            to_url="https://example.test/login",
            allowed=ALLOWED,
        )


def test_a_redirect_to_a_metadata_endpoint_is_refused() -> None:
    with pytest.raises(OriginError):
        assert_redirect_permitted(
            from_url="https://staging.example.test/r",
            to_url="http://169.254.169.254/latest/meta-data/",
            allowed=ALLOWED,
        )


def test_being_approved_at_the_source_buys_nothing_at_the_target() -> None:
    """Stated as a property: the source's membership is irrelevant to the target's."""
    with pytest.raises(OriginError):
        assert_redirect_permitted(
            from_url="http://127.0.0.1:8081/x",  # permitted
            to_url="http://127.0.0.1:9999/x",  # not permitted
            allowed=ALLOWED,
        )


# --- classification ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "loopback"),
    [
        ("http://127.0.0.1:8081", True),
        ("http://localhost:8081", True),
        ("http://[::1]:8081", True),
        ("https://staging.example.test", False),
        ("http://10.0.0.5", False),
    ],
)
def test_loopback_classification(url: str, loopback: bool) -> None:
    assert normalize_origin(url).is_loopback is loopback


@pytest.mark.parametrize(
    ("url", "private"),
    [
        ("http://10.0.0.5", True),
        ("http://192.168.1.1", True),
        ("http://169.254.1.1", True),  # link-local
        ("https://staging.example.test", False),
    ],
)
def test_private_classification_is_information_not_a_verdict(url: str, private: bool) -> None:
    """Local and staging addresses may legitimately be private.

    This is input to a per-environment policy, not a blanket allow or deny — "permit all internal
    networks" is explicitly not an acceptable rule.
    """
    assert normalize_origin(url).is_private is private


def test_origins_are_comparable_and_hashable() -> None:
    assert normalize_origin("http://a.test:80") == Origin("http", "a.test", 80)
    assert len({normalize_origin("http://a.test"), normalize_origin("HTTP://A.TEST:80")}) == 1
