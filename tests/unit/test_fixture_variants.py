"""The seeded defect must be presentational only, and must be real.

Two failure modes are checked here. If the "inaccessible" variant were actually accessible, every
downstream baseline would be a false negative. If it differed from the accessible variant in
anything other than assistive-technology affordances, a candidate repair could be credited for
fixing something that was never the defect.
"""

from __future__ import annotations

import re

import pytest

from reference_app.templates import render_form, template_digest
from reference_app.validation import FieldError

ERRORS = [
    FieldError("email", "Enter an email address in the format name@example.com."),
    FieldError("description", "Description must be at least 10 characters."),
]
VALUES = {"full_name": "Test Person", "email": "bad", "category": "hardware", "description": "x"}


_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _render(variant: str) -> str:
    return render_form(nonce="n1", variant=variant, values=VALUES, errors=ERRORS)


def _markup(variant: str) -> str:
    """Rendered markup with HTML comments removed.

    The variant documents its own seeded defect in a comment that names the very attributes this
    test looks for. Asserting against raw text would match the prose, not the markup.
    """
    return _COMMENT.sub("", _render(variant))


def test_accessible_variant_announces_errors() -> None:
    html = _markup("accessible")
    assert 'role="alert"' in html
    assert 'aria-live="assertive"' in html
    assert 'aria-invalid="true"' in html
    assert 'aria-describedby="email-error"' in html
    assert 'id="email-error"' in html
    assert ".focus()" in html


@pytest.mark.parametrize(
    "affordance",
    ['role="alert"', "aria-live", "aria-invalid", "aria-describedby", ".focus()"],
)
def test_inaccessible_variant_lacks_each_affordance(affordance: str) -> None:
    # If any of these start appearing, the seeded defect has silently healed and every baseline
    # built on it becomes meaningless.
    assert affordance not in _markup("inaccessible")


def test_both_variants_show_the_same_error_text() -> None:
    """The defect is how errors are exposed, never whether they are produced."""
    strip_tags = re.compile(r"<[^>]+>")
    for error in ERRORS:
        assert error.message in strip_tags.sub("", _render("accessible"))
        assert error.message in strip_tags.sub("", _render("inaccessible"))


def test_inaccessible_variant_is_labelled_as_a_seeded_defect() -> None:
    # It must be impossible to mistake the fixture for a discovered customer incident.
    assert "SEEDED DEFECT" in _render("inaccessible")
    assert "SEEDED DEFECT" not in _render("accessible")


def test_template_digest_distinguishes_variants_and_is_stable() -> None:
    a, i = template_digest("accessible"), template_digest("inaccessible")
    assert a != i
    assert a == template_digest("accessible")  # stable across calls
    assert len(a) == 64


def test_user_input_is_escaped_in_both_variants() -> None:
    injected = '"><script>alert(1)</script>'
    for variant in ("accessible", "inaccessible"):
        html = render_form(
            nonce="n1",
            variant=variant,
            values={**VALUES, "full_name": injected},
            errors=ERRORS,
        )
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html


def test_a_receipt_is_not_an_accessibility_claim() -> None:
    """INV-02 in template form.

    The inaccessible variant renders a perfectly ordinary success receipt. Nothing about a
    successful task outcome indicates the journey was operable with a screen reader, so no code
    path may treat a receipt as accessibility evidence.
    """
    strip = re.compile(r"<!--.*?-->", re.S)
    inaccessible = strip.sub("", render_form(nonce="n1", variant="inaccessible", receipt_id="a-1"))
    accessible = strip.sub("", render_form(nonce="n1", variant="accessible", receipt_id="a-1"))

    assert "Request received" in inaccessible
    assert "a-1" in inaccessible
    # Byte-identical success pages: the receipt carries no accessibility signal whatsoever, so
    # nothing downstream may read one as evidence that the journey was operable.
    assert inaccessible == accessible
