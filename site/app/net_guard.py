"""Garde reseau des connecteurs (EPIC-IAF-E18 US18.3, partie logicielle de E9 US9.3).

Toute requete sortante d'un connecteur passe ici : https seul ; hote dans la liste blanche du
connecteur ; TOUTES les adresses resolues doivent etre publiques (privees, loopback, link-local
dont les metadonnees cloud 169.254.169.254, reservees, multicast et non specifiees refusees,
IPv6 mappe IPv4 deballe) ; redirections suivies a la main (3 maximum) et revalidees a chaque
saut ; delai et taille de reponse plafonnes.

Limites assumees (documentees dans l'epic) : pas d'epinglage de l'adresse resolue, donc une
attaque par changement de DNS entre la verification et la connexion n'est pas exclue ;
l'isolation par conteneur dedie (US9.3) n'est pas faite, ce garde tourne dans le processus du site."""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests

MAX_REDIRECTS = 3
DEFAULT_TIMEOUT = 20
# Identification honnete du client (pas un navigateur imite). Constate le 2026-10-03 : l'API OpenRiC de
# reference repond 403 (nginx) au User-Agent par defaut de python-requests et 200 a un User-Agent descriptif.
USER_AGENT = "IAFActory-connector/1.0 (lecture seule)"
DEFAULT_MAX_BYTES = 25 * 1024 * 1024


class NetworkRefused(Exception):
    """Requete refusee par le garde (hote, schema, adresse, redirection ou taille)."""


@dataclass
class GuardedResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes
    url: str
    redirects: list[str] = field(default_factory=list)

    def json(self):
        import json
        return json.loads(self.content.decode("utf-8"))

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


def _check_address(address: str) -> None:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
        or ip.is_multicast or ip.is_unspecified
    ):
        raise NetworkRefused(f"adresse non publique refusee : {ip}")


def validate_url(url: str, allowed_hosts: set[str] | frozenset[str]) -> str:
    """Valide schema, hote et adresses resolues ; renvoie l'hote."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise NetworkRefused("https obligatoire")
    host = (parsed.hostname or "").lower()
    if not host:
        raise NetworkRefused("hote absent")
    if host not in {h.lower() for h in allowed_hosts}:
        raise NetworkRefused(f"hote hors liste blanche : {host}")
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise NetworkRefused(f"hote non resolu : {host}") from None
    if not infos:
        raise NetworkRefused(f"hote non resolu : {host}")
    for info in infos:
        _check_address(info[4][0])
    return host


def guarded_request(
    method: str, url: str, allowed_hosts: set[str] | frozenset[str], *, headers: dict | None = None,
    params: dict | None = None, data: dict | None = None, timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> GuardedResponse:
    headers = {"User-Agent": USER_AGENT, **(headers or {})}
    redirects: list[str] = []
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        validate_url(current, allowed_hosts)
        response = requests.request(
            method, current, headers=headers, params=params if not redirects else None, data=data,
            timeout=timeout, allow_redirects=False, stream=True,
        )
        try:
            if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("Location"):
                current = urljoin(current, response.headers["Location"])
                redirects.append(current)
                continue
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise NetworkRefused(f"reponse trop volumineuse ({declared} octets, maximum {max_bytes})")
            chunks, total = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > max_bytes:
                    raise NetworkRefused(f"reponse trop volumineuse (plus de {max_bytes} octets)")
                chunks.append(chunk)
            return GuardedResponse(
                status_code=response.status_code, headers=dict(response.headers), content=b"".join(chunks),
                url=current, redirects=redirects,
            )
        finally:
            response.close()
    raise NetworkRefused(f"trop de redirections (plus de {MAX_REDIRECTS})")
