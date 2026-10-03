"""Acces Neo4j et passerelle LLM du service d'exposition (lecture seule pour Neo4j)."""
from __future__ import annotations

import json
import re

import requests
from neo4j import Driver, GraphDatabase

from .config import settings

_driver: Driver | None = None


def get_driver() -> Driver:
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(settings.neo4j_uri, auth=("neo4j", settings.neo4j_password))
    return _driver


def embed(text: str) -> list[float]:
    response = requests.post(
        f"{settings.llm_gateway_url}/v1/embeddings",
        headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
        json={"model": settings.embedding_model, "input": text}, timeout=60,
    )
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def chat_json(prompt: str, system: str, max_tokens: int = 1500, timeout: int = 90) -> dict:
    """Reponse JSON stricte du modele (extraction du premier bloc {...}). Raisonnement desactive : sinon les
    jetons de raisonnement tronquent le JSON (mesure du 2026-09-29, voir ADR 0004)."""
    response = requests.post(
        f"{settings.llm_gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
        json={
            "model": settings.answer_model, "temperature": 0, "max_tokens": max_tokens, "reasoning_effort": "disable",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    if content is None:
        raise ValueError("reponse vide du modele")
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise ValueError("pas de JSON dans la reponse du modele")
    return json.loads(match.group(0))
