"""Form markup in two variants.

Both variants post to the same endpoint and are validated identically by the server. They differ
only in how a validation error is exposed to assistive technology:

  accessible    errors land in a role="alert" live region, each field carries aria-invalid and
                aria-describedby pointing at its own message, and focus moves to the first
                invalid field.

  inaccessible  the same errors are rendered as plain styled text with no live region, no
                programmatic association to the field, and no focus movement. A sighted user sees
                the errors immediately; a screen-reader user is given no announcement and no way
                to reach them other than re-reading the whole form.

The inaccessible variant is a *labelled seeded defect*, not a discovered customer incident, and
documents itself as such in the page source.
"""

from __future__ import annotations

import hashlib
import html

from .validation import ALLOWED_CATEGORIES, FieldError

_BASE_STYLE = """
  :root { color-scheme: light dark; }
  body { font: 16px/1.5 system-ui, sans-serif; margin: 0; padding: 2rem; max-width: 40rem; }
  label { display: block; font-weight: 600; margin-top: 1.25rem; }
  input, select, textarea { width: 100%; padding: .5rem; margin-top: .25rem;
                            font: inherit; box-sizing: border-box; }
  button { margin-top: 1.5rem; padding: .6rem 1.2rem; font: inherit; }
  .error { color: #b3261e; margin-top: .25rem; }
  .summary { border: 2px solid #b3261e; padding: .75rem 1rem; margin-bottom: 1rem; }
  .receipt { border: 2px solid #1b5e20; padding: .75rem 1rem; }
"""


def _field(
    *,
    name: str,
    label: str,
    kind: str,
    value: str,
    error: str | None,
    accessible: bool,
) -> str:
    described_by = ""
    invalid = ""
    if error and accessible:
        described_by = f' aria-describedby="{name}-error"'
        invalid = ' aria-invalid="true"'

    if kind == "select":
        options = "".join(
            f'<option value="{html.escape(c)}"{" selected" if c == value else ""}>'
            f"{html.escape(c)}</option>"
            for c in ("", *ALLOWED_CATEGORIES)
        )
        control = f'<select id="{name}" name="{name}"{described_by}{invalid}>{options}</select>'
    elif kind == "textarea":
        control = (
            f'<textarea id="{name}" name="{name}" rows="5"{described_by}{invalid}>'
            f"{html.escape(value)}</textarea>"
        )
    else:
        control = (
            f'<input id="{name}" name="{name}" type="{kind}" value="{html.escape(value)}"'
            f"{described_by}{invalid}>"
        )

    message = ""
    if error:
        # The accessible variant associates the message with the field; the inaccessible variant
        # renders the identical text with no id and no association.
        ident = f' id="{name}-error"' if accessible else ""
        message = f'<p class="error"{ident}>{html.escape(error)}</p>'

    return f'<label for="{name}">{html.escape(label)}</label>{control}{message}'


def render_form(
    *,
    nonce: str,
    variant: str,
    values: dict[str, str] | None = None,
    errors: list[FieldError] | None = None,
    receipt_id: str | None = None,
) -> str:
    accessible = variant == "accessible"
    values = values or {}
    by_field = {e.field: e.message for e in (errors or [])}

    if receipt_id:
        body = (
            f'<div class="receipt" role="status">'
            f"<h2>Request received</h2>"
            f"<p>Your reference number is <strong>{html.escape(receipt_id)}</strong>.</p>"
            f"</div>"
        )
    else:
        summary = ""
        if by_field:
            items = "".join(f"<li>{html.escape(m)}</li>" for m in by_field.values())
            if accessible:
                summary = (
                    f'<div class="summary" role="alert" aria-live="assertive" tabindex="-1" '
                    f'id="error-summary"><h2>There is a problem</h2><ul>{items}</ul></div>'
                )
            else:
                # Seeded defect: visually identical, silent to assistive technology.
                summary = f'<div class="summary"><h2>There is a problem</h2><ul>{items}</ul></div>'

        fields = "".join(
            [
                _field(
                    name="full_name",
                    label="Full name",
                    kind="text",
                    value=values.get("full_name", ""),
                    error=by_field.get("full_name"),
                    accessible=accessible,
                ),
                _field(
                    name="email",
                    label="Email address",
                    kind="text",
                    value=values.get("email", ""),
                    error=by_field.get("email"),
                    accessible=accessible,
                ),
                _field(
                    name="category",
                    label="Category",
                    kind="select",
                    value=values.get("category", ""),
                    error=by_field.get("category"),
                    accessible=accessible,
                ),
                _field(
                    name="description",
                    label="Description",
                    kind="textarea",
                    value=values.get("description", ""),
                    error=by_field.get("description"),
                    accessible=accessible,
                ),
            ]
        )
        focus_script = ""
        if by_field and accessible:
            first = next(iter(by_field))
            focus_script = f"<script>document.getElementById({first!r}).focus();</script>"
        body = (
            f"{summary}"
            f'<form method="post" action="/form/{html.escape(nonce)}" novalidate>'
            f"{fields}"
            f'<button type="submit">Submit request</button>'
            f"</form>{focus_script}"
        )

    defect_note = (
        "<!-- SEEDED DEFECT (labelled fixture variant, not a customer incident): validation "
        "errors are rendered without a live region, without aria-invalid/aria-describedby "
        "association, and without focus movement. Server-side validation is unchanged. -->"
        if not accessible
        else "<!-- Reference accessible behaviour: live-region announcement, programmatic "
        "error association, focus moved to the first invalid field. -->"
    )

    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Service request</title><style>{_BASE_STYLE}</style></head>"
        f"<body>{defect_note}<h1>Service request</h1>{body}</body></html>"
    )


def template_digest(variant: str) -> str:
    """Digest of the fixture template itself.

    Recorded separately from a per-run fixture nonce so a template change is distinguishable from
    a new run of an unchanged template.
    """
    rendered = render_form(nonce="DIGEST", variant=variant)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()
