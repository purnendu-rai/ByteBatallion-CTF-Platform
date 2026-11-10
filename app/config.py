"""Platform configuration, read from environment variables / .env.

Never commit the real `.env`; only `.env.example` lives in the repo.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/bytebatallion.db"
    # Used from Phase 1 to sign session cookies.
    secret_key: str = "dev-only-secret-change-me"
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False
    # dev | prod — "prod" enables the Secure flag on session cookies
    environment: str = "dev"
    # First-admin bootstrap for hosts without shell access (e.g. Render
    # free tier): when all three are set, the app creates this admin on
    # startup if the username is not already taken. NEVER commit real
    # values — set them in the host dashboard, not in render.yaml or .env.
    bootstrap_admin_username: str | None = None
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance so env vars are read once per process."""
    return Settings()


def validate_settings(s: Settings) -> None:
    """Refuse to start a production deployment with unsafe configuration.

    Called once from run.py (and tested directly): in `prod` the platform
    must run on a real secret (the dev default would let anyone forge
    session cookies) and must not point DEBUG on.
    """
    if s.environment != "prod":
        return
    if s.secret_key == "dev-only-secret-change-me" or len(s.secret_key) < 32:
        raise RuntimeError(
            "Refusing to start in prod: SECRET_KEY is missing/short. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    if s.debug:
        raise RuntimeError("Refusing to start in prod: DEBUG=true is not allowed "
                           "(debug mode can leak details inside errors).")
