"""Acces au graph RAG (Neo4j, IAF-E3) et a la passerelle LLM (ADR 0004),
partages par le pipeline d'ingestion (pipeline.py) et l'interrogation des
agents (routers/ask.py). Client HTTP et driver Neo4j portes depuis
poc/common.py et poc/llm.py (preuve de bout en bout, 2026-09-27)."""
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
    r = requests.post(
        f"{settings.llm_gateway_url}/v1/embeddings",
        headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
        json={"model": settings.embedding_model, "input": text},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def chat(prompt: str, model: str, system: str | None = None, timeout: int = 90, max_tokens: int = 1200) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    r = requests.post(
        f"{settings.llm_gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
        # max_tokens borne la generation : un modele local peut partir en boucle
        # (repetitions) et faire tourner l'appel bien au-dela du raisonnable
        # (mesure poc/RESULTATS.md). Releve a 1200 le 2026-09-28 (passage a
        # gemini-vertex-flash) : les modeles Gemini 2.5 consomment des jetons
        # de "raisonnement" internes qui comptent dans max_tokens - constate
        # reellement (reponse vide, finish_reason "length", avec max_tokens=10
        # entierement consomme par le raisonnement avant tout texte).
        json={"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens},
        timeout=timeout,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    if content is None:
        # Vu reellement avec gemini-vertex-flash : max_tokens trop bas pour
        # depasser les jetons de raisonnement internes -> reponse vide plutot
        # qu'une exception cote passerelle. Remonte comme une erreur normale
        # (les appelants traitent deja un echec de chat() comme non bloquant).
        raise ValueError("reponse vide du modele (max_tokens probablement insuffisant face au raisonnement interne)")
    return content


def chat_json(prompt: str, model: str, system: str | None = None, timeout: int = 90) -> dict:
    """Demande une reponse JSON stricte ; les petits modeles locaux entourent
    parfois le JSON de texte ou de ``` : on extrait le premier bloc {...}."""
    raw = chat(prompt, model=model, system=system, timeout=timeout)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"pas de JSON dans la reponse du modele : {raw!r}")
    return json.loads(match.group(0))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"dimensions incompatibles : {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
