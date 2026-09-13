"""Logical fixture identity. Presentation changes are bound by separate source/artifact hashes."""

from __future__ import annotations

from .fixture_contract import REFERENCE_FIXTURE_DIGEST


def template_digest(variant: str) -> str:
    """Both presentation variants exercise the same frozen backend task definition.

    A digest is a declaration, not proof the candidate obeyed it. The independent protected
    runner compares this declaration and separately observes actual HTTP/database behavior.
    """
    if variant not in ("accessible", "inaccessible"):
        raise ValueError("unknown reference presentation variant")
    return REFERENCE_FIXTURE_DIGEST
