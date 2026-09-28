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

    # Neo4j (graph RAG, IAF-E3) : memes conventions que Postgres ci-dessus -
    # 127.0.0.1 par defaut pour un usage local hors Docker (port NEO4J_BOLT_PORT
    # du .env racine, deja decale sur cette machine) ; `neo4j_host` surcharge en
    # `neo4j`:7687 (port interne fixe) dans compose.yaml.
    neo4j_host: str = "127.0.0.1"
    neo4j_port: int = 7687
    neo4j_password: str

    # Passerelle LLM (ADR 0004). Alias existants de infra/llm-gateway/litellm.yaml,
    # jamais un nom de modele en dur. Defaut = "ollama-local" (local, gratuit,
    # deja mesure dans poc/RESULTATS.md) plutot qu'un alias iaf-* facturable :
    # aucun budget par agent (US11.3) n'est encore appliques par ce site, un
    # defaut payant serait une depense non bornee.
    llm_gateway_host: str = "127.0.0.1"
    llm_gateway_port: int = 4000
    litellm_master_key: str
    embedding_model: str = "iaf-embedding"
    extraction_model: str = "ollama-local"
    answer_model: str = "ollama-local"

    # Documents deposes par les creators (US3.1). Volume dedie dans
    # compose.yaml ; chemin local par defaut pour le dev hors Docker.
    documents_dir: Path = Path("./data/documents")

    # Seuil de similarite cosinus pour rattacher un document a une classe
    # existante plutot que d'en creer une nouvelle (US7.4/US7.5). Provisoire et
    # NON calibre (aucune mesure hors echantillon, US7.7) : un seul signal
    # (embeddings du document entier), pas la cascade complete de l'epic E7.
    recognition_threshold: float = 0.75

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def neo4j_uri(self) -> str:
        return f"bolt://{self.neo4j_host}:{self.neo4j_port}"

    @property
    def llm_gateway_url(self) -> str:
        return f"http://{self.llm_gateway_host}:{self.llm_gateway_port}"


settings = Settings()
