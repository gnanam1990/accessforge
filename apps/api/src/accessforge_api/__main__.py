from __future__ import annotations

import uvicorn

from .app import create_app
from .config import ApiSettings


def main() -> None:
    settings = ApiSettings()  # type: ignore[call-arg]
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
