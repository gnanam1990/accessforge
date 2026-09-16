"""Optional immutable public web snapshot; never a general filesystem-serving endpoint."""

from __future__ import annotations

import hashlib
from pathlib import Path

from starlette.requests import Request
from starlette.responses import Response

MEDIA = {
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}
MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024


class StaticWeb:
    """Load only index.html and known assets once from an operator-owned release directory.

    Requests perform dictionary lookups, never filesystem reads. Changed files need an explicit
    process restart, avoiding mixed-release pages/assets and request-time symlink traversal.
    Configuration must point to trusted built assets, never a candidate workspace.
    """

    def __init__(self, root: Path) -> None:
        if not root.is_absolute() or root.resolve() != root or not root.is_dir():
            raise ValueError("web_dist_directory must be an absolute canonical build directory")
        self.files: dict[str, tuple[bytes, str, str]] = {}
        total = 0

        def load(path: Path, media: str) -> None:
            nonlocal total
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE:
                raise ValueError("static web build contains an unavailable or oversized file")
            data = path.read_bytes()
            total += len(data)
            if len(data) > MAX_FILE or total > MAX_TOTAL or len(self.files) >= 10000:
                raise ValueError("static web build exceeds snapshot limits")
            self.files[path.relative_to(root).as_posix()] = (
                data,
                media,
                '"' + hashlib.sha256(data).hexdigest() + '"',
            )

        load(root / "index.html", "text/html")
        assets = root / "assets"
        if assets.is_symlink() or not assets.is_dir():
            raise ValueError("static web assets directory is missing")
        for path in sorted(assets.rglob("*")):
            if path.is_symlink():
                raise ValueError("static web symlinks are refused")
            if path.is_file() and path.suffix in MEDIA:
                load(path, MEDIA[path.suffix])

    async def __call__(self, request: Request) -> Response:
        path = str(request.path_params.get("path", ""))
        content = self.files.get(path) if path.startswith("assets/") else None
        if content is None:
            # Explicit SPA entry points only. Unknown API/health routes, .env, source maps and
            # missing asset URLs must never quietly become an HTML 200 response.
            parts = path.split("/")
            navigation = path in {"", "index.html", "workspaces"} or (
                path.startswith("w/") and all(part and "." not in part for part in parts)
            )
            if not navigation:
                return Response(status_code=404)
            content = self.files["index.html"]
        data, media, etag = content
        headers = {
            "Cache-Control": "no-cache",
            "ETag": etag,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
            "X-Frame-Options": "DENY",
        }
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        headers["Content-Length"] = str(len(data))
        return Response(
            content=b"" if request.method == "HEAD" else data,
            media_type=media,
            headers=headers,
        )
