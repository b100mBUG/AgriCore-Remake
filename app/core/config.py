"""
app/core/config.py — Application settings via pydantic-settings.

All configuration is read from environment variables or a .env file.
Access settings anywhere via:

    from app.core.config import settings
    print(settings.database_url)

Never hard-code secrets. Every value with a default here is a safe
fallback for development — override everything in production .env.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",          # silently ignore unknown env vars
        case_sensitive=False,
    )

    # ── App ───────────────────────────────────────────────────────────────────
    app_env: str = "development"
    app_version: str = "1.0.0"

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./agricore_dev.db"

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Security ──────────────────────────────────────────────────────────────
    secret_key: str = "dev-secret-change-in-production"
    access_token_expire_minutes: int = 10080   # 7 days

    # Admin login — single admin account for this MVP (see app/routes/admin.py).
    # admin_password_hash is a bcrypt hash, generated once via:
    #   python -c "from app.core.security import hash_password; print(hash_password('your-password'))"
    # Never put a plain-text password here.
    admin_email: str = "admin@agricore.co.ke"
    admin_password_hash: str = ""

    # ── AI ────────────────────────────────────────────────────────────────────
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # ── Weather ───────────────────────────────────────────────────────────────
    weather_api_key: str = ""

    # ── Cloudinary ────────────────────────────────────────────────────────────
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""

    # ── Crawler ───────────────────────────────────────────────────────────────
    crawl_interval_hours: int = 24
    classify_interval_minutes: int = 60
    max_crawl_pages_per_run: int = 100
    classifier_confidence_threshold: float = 0.70

    # ── CORS ──────────────────────────────────────────────────────────────────
    allowed_origins: str = "*"

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    rate_limit_per_minute: int = 60

    # ── Derived helpers ───────────────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_sqlite(self) -> bool:
        """True when running against SQLite (dev). Used to conditionally
        skip PostgreSQL-specific connect_args like ssl=True."""
        return self.database_url.startswith("sqlite")

    @property
    def origins_list(self) -> list[str]:
        """Parsed CORS origins.

        Browsers reject `Access-Control-Allow-Origin: *` combined with
        `Access-Control-Allow-Credentials: true` outright — main.py sets
        allow_credentials=True, so a literal "*" here would silently
        break all cross-origin requests rather than "be open." If you
        haven't set explicit origins yet, this returns an empty list so
        the failure is loud (CORS blocks everything) instead of a
        same-origin-looking pass that quietly does nothing in real
        browsers. Set ALLOWED_ORIGINS to your actual frontend origin(s)
        before deploying.
        """
        raw = [o.strip() for o in self.allowed_origins.split(",") if o.strip()]
        if raw == ["*"]:
            return []
        return raw


# Module-level singleton — import this everywhere.
settings = Settings()
