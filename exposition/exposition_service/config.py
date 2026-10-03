"""Configuration du service d'exposition : variables d'environnement (memes noms que le site pour Neo4j et
la passerelle LLM) ; le `.env` racine est lu hors Docker, pour les tests sur la machine de dev."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env",), env_file_encoding="utf-8", extra="ignore")

    neo4j_host: str = "127.0.0.1"
    neo4j_port: int = 7687
    neo4j_password: str = ""

    llm_gateway_host: str = "127.0.0.1"
    llm_gateway_port: int = 4000
    litellm_master_key: str = ""
    embedding_model: str = "iaf-embedding"
    answer_model: str = "gemini-vertex-flash"

    # Jeton partage avec le site : tout appel MCP sans "Authorization: Bearer <jeton>" est refuse. Vide =
    # le serveur refuse de demarrer (jamais de service ouvert par defaut).
    exposition_token: str = ""

    # Recuperation (deterministe). Mesure du 2026-10-03 : le score vectoriel (nomic-embed-text via Neo4j)
    # ne discrimine pas la pertinence (recette de tarte 0,835 contre 0,84 a 0,90 pour des questions
    # pertinentes) ; un passage n'est donc retenu que s'il contient assez des termes de la question.
    vector_candidates: int = 30
    max_passages: int = 5
    min_term_coverage: float = 0.5
    max_matches: int = 25

    @property
    def neo4j_uri(self) -> str:
        return f"bolt://{self.neo4j_host}:{self.neo4j_port}"

    @property
    def llm_gateway_url(self) -> str:
        return f"http://{self.llm_gateway_host}:{self.llm_gateway_port}"


settings = Settings()
