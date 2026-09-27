"""Configuration du site. Lit le `.env` a la racine du depot (memes secrets
que le reste de l'infra) plus un `site/.env` optionnel propre au site. Quand
ces fichiers n'existent pas (dans l'image Docker), les valeurs viennent des
variables d'environnement passees par `compose.yaml`. Aucun secret n'a de
valeur par defaut en dur.

POSTGRES_HOST/POSTGRES_PORT valent par defaut 127.0.0.1:5432 (usage local,
hors Docker, ou POSTGRES_PORT vient du `.env` racine et peut avoir ete
decale pour eviter un conflit de port sur la machine). Dans `compose.yaml`,
le service `site` les surcharge en `postgres`:5432 (reseau interne Docker,
jamais le port hote remappe)."""
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

    postgres_host: str = "127.0.0.1"
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
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
