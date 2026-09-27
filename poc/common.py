"""Configuration partagee du PoC : lit .env a la racine du depot (aucune
dependance a python-dotenv : on ne veut pas l'ajouter juste pour ca)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_path = ROOT / ".env"
    if not env_path.exists():
        raise SystemExit(f"{env_path} introuvable : lancer scripts/init-env.ps1 d'abord.")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


_ENV = _load_env()


def env(key: str, default: str | None = None) -> str:
    value = os.environ.get(key) or _ENV.get(key) or default
    if value is None:
        raise SystemExit(f"variable {key} absente de .env")
    return value


GATEWAY_URL = f"http://127.0.0.1:{env('LLM_GATEWAY_PORT', '4000')}"
GATEWAY_KEY = env("LITELLM_MASTER_KEY")

NEO4J_URI = f"bolt://127.0.0.1:{env('NEO4J_BOLT_PORT', '7687')}"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = env("NEO4J_PASSWORD")

CHAT_MODEL = "ollama-local"
EMBEDDING_MODEL = "iaf-embedding"
