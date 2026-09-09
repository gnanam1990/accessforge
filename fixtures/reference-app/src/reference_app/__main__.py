"""Start the reference application. Loopback binding is enforced by configuration."""

from __future__ import annotations

import uvicorn

from . import db
from .app import create_app
from .config import ReferenceAppSettings


def main() -> None:
    settings = ReferenceAppSettings()  # type: ignore[call-arg]
    db.initialize(settings.database_url)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
