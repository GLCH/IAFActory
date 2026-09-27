"""Configuration du site. Lit le `.env` a la racine du depot (memes secrets
que le reste de l'infra : POSTGRES_*) plus un `site/.env` optionnel propre au
site (SESSION_SECRET). Aucun secret n'a de valeur par defaut en dur."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SITE_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", SITE_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_port: int = 5432

    session_secret: str
    session_ttl_hours: int = 12

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@127.0.0.1:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
