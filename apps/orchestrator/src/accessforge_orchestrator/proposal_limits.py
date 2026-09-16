"""Local proposal admission limits, independent of any provider's agent SDK."""

from typing import TypedDict


class Limits(TypedDict, total=False):
    """One-turn admission envelope; token bounds do not cap provider spending or retries."""

    turns: int
    output_tokens: int
    total_tokens: int
