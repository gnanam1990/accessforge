"""Differential test: Python and TypeScript must agree on inputs nobody wrote down.

The shared vectors prove agreement on cases we thought of. This proves agreement on cases we did
not — which is where a canonicalization divergence would actually hide.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from accessforge_domain.canonical import CanonicalizationError, canonicalize, digest

ROOT = Path(__file__).resolve().parents[2]
TS_DIST = ROOT / "packages/contracts/ts/dist/index.js"

# Deliberately awkward: mixed scripts, astral-plane characters, every C0 control character, key
# orders that differ between code-point and code-unit sorting, nesting, and the safe-integer
# boundaries. Characters that are awkward to type are constructed, never pasted.
ALL_C0 = "".join(chr(c) for c in range(0x20))
DEL = chr(0x7F)

PAYLOADS: list[dict] = [
    {},
    {"a": 1},
    {"b": 1, "a": 2, "C": 3, "A": 4},
    {"ä": 1, "z": 2, "Z": 3},
    {chr(0x1F600): 1, chr(0xFF00): 2, "": 3},
    {"s": ALL_C0},
    {"s": 'quote:" backslash:\\ slash:/ del:' + DEL},
    {"nested": {"deep": {"deeper": [1, {"k": None}, True, False]}}},
    {"arr": [[], [[]], [{}, {"a": []}]]},
    {"max": 9007199254740991, "min": -9007199254740991, "zero": 0, "negzero": -0.0},
    {"mixed": [1, "two", None, True, {"three": 3}]},
    {"ta": "தமிழ்", "emoji": chr(0x1F9EA) + chr(0x1F4C4)},
    {"rfc3339": "2026-09-09T12:00:00Z", "offset": "2026-09-09T12:00:00+05:30"},
    {"empty_string_key": {"": "value"}},
    # Precomposed U+00E9 versus "e" plus combining acute: distinct keys that look identical.
    {"unicode_keys": {chr(0x00E9): 1, "e" + chr(0x0301): 2}},
]


def _require_built_ts() -> None:
    if shutil.which("node") is None:
        pytest.fail("node is required for the cross-language agreement test")
    if not TS_DIST.exists():
        pytest.fail(
            f"TypeScript build output missing at {TS_DIST}. "
            "Run `pnpm --filter @accessforge/contracts build` first. "
            "This test fails rather than skips: an unbuilt implementation cannot be reported as "
            "agreeing with anything."
        )


def _typescript_results(payloads: list[dict]) -> list[dict[str, str]]:
    # Payloads arrive on stdin rather than argv: process.argv is indexed differently under
    # `node -e`, and stdin also avoids argument-length limits as the payload set grows.
    script = (
        f"import {{ canonicalize, digest }} from {json.dumps(str(TS_DIST))};\n"
        "let raw = '';\n"
        "process.stdin.setEncoding('utf8');\n"
        "for await (const chunk of process.stdin) raw += chunk;\n"
        "const payloads = JSON.parse(raw);\n"
        "const out = payloads.map((p) => ({ canonical: canonicalize(p), digest: digest(p) }));\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    proc = subprocess.run(  # noqa: S603
        ["node", "--input-type=module", "-e", script],  # noqa: S607
        input=json.dumps(payloads),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        pytest.fail(f"TypeScript canonicalizer failed: {proc.stderr.strip()}")
    return list(json.loads(proc.stdout))


def test_both_languages_produce_identical_canonical_form_and_digest() -> None:
    _require_built_ts()
    ts = _typescript_results(PAYLOADS)
    assert len(ts) == len(PAYLOADS)

    mismatches = []
    for payload, ts_result in zip(PAYLOADS, ts, strict=True):
        py_canonical = canonicalize(payload)
        py_digest = digest(payload)
        if py_canonical != ts_result["canonical"] or py_digest != ts_result["digest"]:
            mismatches.append(
                {
                    "payload": repr(payload),
                    "python": {"canonical": py_canonical, "digest": py_digest},
                    "typescript": ts_result,
                }
            )

    assert not mismatches, "cross-language canonicalization divergence:\n" + json.dumps(
        mismatches, indent=2, ensure_ascii=False
    )


def test_both_languages_reject_lone_surrogates_identically() -> None:
    """Independent review finding 1.

    Python raised a raw UnicodeEncodeError from inside digest(); TypeScript silently substituted
    U+FFFD and returned a successful, wrong hash. Agreement here means agreeing to refuse.
    """
    _require_built_ts()
    for text in (chr(0xD800), chr(0xDFFF), chr(0xD83D) + "a", "a" + chr(0xDE00)):
        with pytest.raises(CanonicalizationError):
            digest({"s": text})

        # The payload cannot survive a JSON round-trip to the subprocess intact, so drive the
        # TypeScript side with an escaped literal it decodes itself.
        escaped = "".join(f"\\u{ord(c):04x}" for c in text)
        script = (
            f"import {{ digest, CanonicalizationError }} from {json.dumps(str(TS_DIST))};\n"
            f'const s = "{escaped}";\n'
            "try { digest({ s }); process.stdout.write('ACCEPTED'); }\n"
            "catch (e) { process.stdout.write(e instanceof CanonicalizationError "
            "? 'REFUSED' : 'WRONG_ERROR:' + e.constructor.name); }\n"
        )
        proc = subprocess.run(  # noqa: S603
            ["node", "--input-type=module", "-e", script],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == "REFUSED", f"TypeScript did not refuse {text!r}: {proc.stdout}"


def test_the_differential_harness_can_actually_detect_a_difference() -> None:
    """Self-test.

    If the harness compared nothing — an empty payload list, a silently failing subprocess — the
    test above would pass while proving nothing. This confirms it distinguishes agreement from
    disagreement.
    """
    _require_built_ts()
    ts = _typescript_results([{"a": 1}, {"a": 2}])
    assert ts[0]["digest"] != ts[1]["digest"]
    assert ts[0]["digest"] == digest({"a": 1})
    assert ts[1]["digest"] == digest({"a": 2})
