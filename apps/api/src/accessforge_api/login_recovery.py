"""Static browser recovery for login refusals; never reveal provider or account details."""

from html import escape

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response


def browser_login_recovery(request: Request, problem: Response, request_id: str) -> Response:
    """Keep JSON for clients and preserve the refusal's status and headers for browsers."""
    if request.url.path not in {"/v1/auth/github/start", "/v1/auth/github/callback"}:
        return problem
    if problem.status_code not in {401, 503} or request.headers.get("sec-fetch-dest") != "document":
        return problem
    # Native browser navigation advertises this exact media type. Explicit q=0 refuses HTML.
    accepts_html = False
    for media in request.headers.get("accept", "").lower().split(","):
        parts = [part.strip() for part in media.split(";")]
        if parts[0] != "text/html":
            continue
        try:
            quality = next((float(part[2:]) for part in parts[1:] if part.startswith("q=")), 1.0)
        except ValueError:
            continue
        accepts_html = 0 < quality <= 1
        break
    if not accepts_html:
        return problem
    title = (
        "GitHub sign-in did not complete"
        if problem.status_code == 401
        else "Sign-in is temporarily unavailable"
    )
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer"><title>{title} · AccessForge</title>
<style>
:root{{color-scheme:light dark;font:100%/1.6 system-ui,sans-serif}}
body{{margin:0;background:#10141d;color:#eef2ff}}
main{{max-width:38rem;margin:8vh auto;padding:2rem}}
h1{{font-size:clamp(1.7rem,5vw,2.4rem);line-height:1.2}}li{{margin-block:.75rem}}
a{{color:#c3d2ff;text-underline-offset:.2em}}
a:focus-visible{{outline:3px solid #9ddcff;outline-offset:5px}}
nav{{display:flex;flex-wrap:wrap;gap:1rem;margin-block:1.5rem}}
nav a{{padding:.75rem 1rem;border:1px solid #9aabd4;border-radius:.5rem}}
small{{overflow-wrap:anywhere}}@media(max-width:40rem){{main{{margin:2vh auto;padding:1.25rem}}}}
</style></head><body><main><p>ACCESSFORGE · SIGN-IN</p><h1>{title}</h1>
<p>No new AccessForge session was created by this attempt.
This message does not identify which account has access.</p>
<ol><li>Check the GitHub account signed in to this browser profile.
Use the account your workspace owner linked or invited.</li>
<li>If you are joining through an invitation, reopen the original invitation link.
New users must choose the invitation's create-account option before accepting membership.</li>
<li>If you cancelled sign-in, the link expired, or the service was unavailable,
start again from sign-in. Do not refresh or reuse this callback URL.</li></ol>
<nav aria-label="Sign-in recovery"><a href="/">Return to sign-in</a>
<a href="https://github.com/" rel="noreferrer">Check GitHub account</a></nav>
<p>Signing in does not automatically grant workspace access.
Ask your workspace owner if your account still cannot sign in.</p>
<small>Support reference: {escape(request_id)}</small></main></body></html>"""
    headers = {
        key: value
        for key, value in problem.headers.items()
        if key.lower() not in {"content-length", "content-type"}
    }
    headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    )
    headers["X-Content-Type-Options"] = "nosniff"
    return HTMLResponse(body, status_code=problem.status_code, headers=headers)
