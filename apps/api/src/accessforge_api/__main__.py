from __future__ import annotations

import uvicorn

from .app import create_app
from .config import ApiSettings


def main() -> None:
    settings = ApiSettings()  # type: ignore[call-arg]
    # Structured route-template telemetry already records requests. Raw server
    # access logs would expose OAuth codes/state in callback query strings.
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
