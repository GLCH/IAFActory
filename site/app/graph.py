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


def chat(
    prompt: str, model: str, system: str | None = None, timeout: int = 90, max_tokens: int = 1200,
    reasoning_effort: str | None = "disable",
) -> str:
    """`reasoning_effort="disable"` par defaut (ajoute le 2026-09-29) - CAUSE
    RACINE trouvee et verifiee, pas juste une nouvelle hausse de max_tokens :
    sur un document reel dense (LaTeX academique, jargon technique), Gemini
    2.5 Flash consommait jusqu'a 87% du budget de completion en jetons de
    "raisonnement" INVISIBLES avant tout texte utile (mesure directe du champ
    `usage.completion_tokens_details.reasoning_tokens` de la reponse - ex.
    2913 jetons de raisonnement pour 412 de texte reel sur un prompt court),
    d'ou les troncatures JSON persistantes meme apres plusieurs hausses de
    max_tokens (4000, 8000...) qui ne visaient QUE le symptome. `reasoning_effort:
    "disable"` (parametre LiteLLM, verifie via sa documentation officielle -
    mappe sur `thinking.budget_tokens: 0` cote Vertex AI, pas devine) supprime
    entierement ces jetons de raisonnement : reponse complete et rapide sur le
    meme prompt qui tronquait avant, verifie reellement (`reasoning_tokens`
    absent du usage, JSON bien forme). Nos usages (extraction structuree,
    reponse courte) n'ont jamais eu besoin d'un raisonnement etendu - laisse
    overridable (None = comportement par defaut du modele) si un futur appel
    en avait vraiment besoin."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    r = requests.post(
        f"{settings.llm_gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
        # max_tokens borne la generation : un modele local peut partir en boucle
        # (repetitions) et faire tourner l'appel bien au-dela du raisonnable
        # (mesure poc/RESULTATS.md).
        json=payload,
        timeout=timeout,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    if content is None:
        # Vu reellement avec gemini-vertex-flash avant l'ajout de
        # reasoning_effort="disable" ci-dessus : max_tokens trop bas face aux
        # jetons de raisonnement internes -> reponse vide plutot qu'une
        # exception cote passerelle. Devrait etre rare desormais ; remonte
        # comme une erreur normale (les appelants traitent deja un echec de
        # chat() comme non bloquant) au cas ou.
        raise ValueError("reponse vide du modele (max_tokens probablement insuffisant face au raisonnement interne)")
    return content


def chat_json(
    prompt: str, model: str, system: str | None = None, timeout: int = 90, max_tokens: int = 1200,
    reasoning_effort: str | None = "disable",
) -> dict:
    """Demande une reponse JSON stricte ; les petits modeles locaux entourent
    parfois le JSON de texte ou de ``` : on extrait le premier bloc {...}.
    `max_tokens` par defaut = celui de `chat()`. `reasoning_effort="disable"`
    par defaut (voir chat() ci-dessus pour la mesure reelle qui l'a motive) -
    la cause principale des troncatures JSON persistantes malgre plusieurs
    hausses de max_tokens (4000, 8000...) sur des documents reels denses."""
    raw = chat(prompt, model=model, system=system, timeout=timeout, max_tokens=max_tokens, reasoning_effort=reasoning_effort)
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
