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
    # Turso (hosted SQLite) auth token. Only used when DATABASE_URL is a
    # libsql:// URL. NEVER commit a real token — set it in the host
    # dashboard, not in render.yaml or .env.
    turso_auth_token: str | None = None
    # Backblaze B2 (S3-compatible) file storage — v3.3. When
    # B2_BUCKET_NAME is set, challenge files live in B2 instead of the
    # host's ephemeral disk; when unset, files stay on local disk (the
    # pre-v3.3 behaviour). All four must be set together — a partial
    # config refuses to build a client with a clear error. NEVER commit
    # real values — set them in the host dashboard, never in .env.
    b2_bucket_name: str | None = None
    b2_key_id: str | None = None
    b2_application_key: str | None = None
    b2_endpoint: str | None = None
    # Transactional email — v3.5. Brevo HTTPS API (Render free blocks
    # outbound SMTP ports 25/465/587, so SMTP cannot work there).
    # BREVO_API_KEY is the v3 API key (starts with xkeysib-). EMAIL_FROM
    # is the verified Brevo sender — a plain email address works, no own
    # domain needed. PUBLIC_URL is the base URL baked into email links.
    # NEVER commit real values — set them in the host dashboard.
    brevo_api_key: str | None = None
    email_from: str = "ByteBatallion <iampurnendurai@gmail.com>"
    public_url: str = "https://bytebatallion.onrender.com"


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
