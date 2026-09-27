"""Petit client HTTP vers la passerelle LLM (chat), partage par les scripts."""
from __future__ import annotations

import json
import re

import requests

from common import CHAT_MODEL, GATEWAY_KEY, GATEWAY_URL


def chat(prompt: str, system: str | None = None, timeout: int = 90, max_tokens: int = 500) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    r = requests.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        headers={"Authorization": f"Bearer {GATEWAY_KEY}"},
        # max_tokens borne la generation : un modele local peut partir en boucle
        # (repetitions) et faire tourner l'appel bien au-dela du raisonnable.
        json={"model": CHAT_MODEL, "messages": messages, "temperature": 0, "max_tokens": max_tokens},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def chat_json(prompt: str, system: str | None = None, timeout: int = 90) -> dict:
    """Demande une reponse JSON stricte ; les petits modeles locaux entourent
    parfois le JSON de texte ou de ``` : on extrait le premier bloc {...}."""
    raw = chat(prompt, system=system, timeout=timeout)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"pas de JSON dans la reponse du modele : {raw!r}")
    return json.loads(match.group(0))
