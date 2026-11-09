"""uvicorn entrypoint: python run.py"""

import uvicorn

from app.config import get_settings, validate_settings


def main() -> None:
    settings = get_settings()
    validate_settings(settings)  # refuse an unsafe prod configuration
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
