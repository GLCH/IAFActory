"""Logging applicatif pousse vers Grafana Loki - demande explicite le
2026-09-29 : "Ajoute un logging applicatif qui doit etre pousse vers un
grafana. Genere le code, l'infra et tout ce qu'il faut mais ne l'active pas."

INERT par defaut : `configure_logging()` n'installe le handler Loki QUE si
`settings.loki_url` est renseignee (vide par defaut, config.py) - sans
configuration explicite, le comportement de logging est identique a avant
(rien de nouveau active). Le service docker-compose correspondant (Loki +
Grafana, compose.yaml) est lui-meme derriere un profil dedie, jamais demarre
par un simple `docker compose up`.

Handler HTTP minimal ecrit a la main plutot qu'une dependance pip
supplementaire (ex. `python-logging-loki`) : l'API push de Loki est simple,
documentee et stable (`POST /loki/api/v1/push`, un flux de lignes horodatees
en nanosecondes avec des labels) - pas besoin d'une bibliotheque tierce pour
ce besoin, et cela evite d'ajouter une dependance non verifiee ici (regle du
projet : ne jamais deviner une API)."""
from __future__ import annotations

import json
import logging
import time

import requests

from .config import settings


class LokiHandler(logging.Handler):
    """Un POST par log (pas de batch/file d'attente - suffisant pour le
    volume de ce service ; a revoir si le volume augmente beaucoup).
    Toute erreur reseau est avalee : le logging ne doit jamais faire
    planter l'application (meme principe que le reste du projet - un
    echec d'un mecanisme annexe n'invalide jamais le travail principal)."""

    def __init__(self, url: str, labels: dict[str, str], timeout: float = 2.0):
        super().__init__()
        self._url = url.rstrip("/") + "/loki/api/v1/push"
        self._labels = labels
        self._timeout = timeout

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = {
                "streams": [{
                    "stream": {**self._labels, "level": record.levelname.lower()},
                    "values": [[str(int(time.time() * 1_000_000_000)), self.format(record)]],
                }],
            }
            requests.post(
                self._url, data=json.dumps(payload),
                headers={"Content-Type": "application/json"}, timeout=self._timeout,
            )
        except Exception:
            pass


def configure_logging() -> None:
    """Appelee au demarrage (main.py). N'ajoute le handler Loki QUE si
    `settings.loki_url` est non vide - inert par defaut."""
    root = logging.getLogger()
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    if not settings.loki_url:
        return
    handler = LokiHandler(settings.loki_url, labels={"service": "iafactory-site"})
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root.addHandler(handler)
