"""Connecteur API OpenRiC (EPIC-IAF-E18 US18.4) : notices RiC-O -> documents Markdown.

API verifiee en direct le 2026-10-03 sur l'instance de reference
`https://ric.theahg.co.za/api/ric/v1` : `GET /records?page=&limit=&level=` renvoie
`{"ric:total", "ric:page", "ric:items": [{"@id", "@type", "rico:identifier", "rico:title"}]}` ;
`GET /records/{slug}` renvoie le detail JSON-LD (rico:title, rico:identifier,
openricx:description, rico:hasExtent, rico:hasOrHadInstantiation...). Lecture publique
d'apres la specification OpenRiC (https://github.com/openric/spec), sans identifiant.

Configuration : {"base_url": "https://hote/api/ric/v1"}. L'hote de `base_url` est la seule entree de
la liste blanche du connecteur. Les relations (/relations-for/{id}) et la hierarchie ne sont pas
reprises en v1. Le contenu rapporte est une DONNEE non fiable (ADR 0003 point 5)."""
from __future__ import annotations

from urllib.parse import urlparse

from ..net_guard import NetworkRefused, guarded_request
from .base import ConnectorError, RemoteFile, safe_filename

PAGE_SIZE_MAX = 100
_USED_KEYS = {"@context", "@id", "@type", "rico:title", "rico:identifier", "openricx:description", "rico:type",
              "rico:hasExtent", "rico:hasOrHadInstantiation"}


def validate_config(config: dict) -> dict:
    base_url = str(config.get("base_url", "")).strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ConnectorError("base_url doit etre une URL https (ex. https://ric.theahg.co.za/api/ric/v1)")
    return {"base_url": base_url}


def allowed_hosts(config: dict) -> set[str]:
    return {urlparse(config["base_url"]).hostname.lower()}


def _scalar(value) -> str | None:
    if isinstance(value, (str, int, float)) and str(value).strip():
        return str(value).strip()
    if isinstance(value, dict):
        for key in ("@value", "rdfs:label", "rico:title", "rico:textualValue", "rico:quantity"):
            if key in value:
                return _scalar(value[key])
    return None


def record_to_markdown(record: dict, fallback_title: str) -> str:
    title = _scalar(record.get("rico:title")) or fallback_title
    lines = [f"# {title}", ""]
    kind = _scalar(record.get("rico:type")) or _scalar(record.get("@type"))
    identifier = _scalar(record.get("rico:identifier"))
    description = _scalar(record.get("openricx:description"))
    if kind:
        lines.append(f"- Niveau de description : {kind.rsplit('#', 1)[-1]}")
    if identifier:
        lines.append(f"- Identifiant : {identifier}")
    extent = record.get("rico:hasExtent")
    if isinstance(extent, dict):
        extent_text = " ".join(filter(None, [_scalar(extent.get("rico:quantity")), _scalar(extent.get("rico:hasExtentType"))]))
        if extent_text:
            lines.append(f"- Etendue : {extent_text}")
    if description:
        lines += ["", "## Description", "", description]
    instantiations = record.get("rico:hasOrHadInstantiation")
    if isinstance(instantiations, dict):
        instantiations = [instantiations]
    if isinstance(instantiations, list) and instantiations:
        lines += ["", "## Instanciations", ""]
        for item in instantiations:
            if not isinstance(item, dict):
                continue
            parts = [_scalar(item.get("rico:identifier")), _scalar(item.get("openricx:hasMimeType"))]
            size = item.get("rico:hasExtent")
            if isinstance(size, dict) and _scalar(size.get("rico:quantity")):
                parts.append(f"{_scalar(size.get('rico:quantity'))} {_scalar(size.get('rico:hasExtentType')) or ''}".strip())
            lines.append("- " + ", ".join(p for p in parts if p))
    extras = []
    for key, value in record.items():
        if key in _USED_KEYS or not key.startswith(("rico:", "openricx:")):
            continue
        text = _scalar(value)
        if text:
            extras.append(f"- {key.split(':', 1)[1]} : {text}")
    if extras:
        lines += ["", "## Autres informations", ""] + extras
    return "\n".join(lines).strip() + "\n"


def fetch_files(config: dict, secret: str | None, limit: int) -> tuple[list[RemoteFile], list[str]]:
    config = validate_config(config)
    hosts = allowed_hosts(config)
    base = config["base_url"]
    errors: list[str] = []
    try:
        listing = guarded_request(
            "GET", f"{base}/records", hosts, params={"limit": min(limit, PAGE_SIZE_MAX), "page": 1},
            headers={"Accept": "application/ld+json, application/json"},
        )
        if not listing.ok:
            raise ConnectorError(f"liste des notices : HTTP {listing.status_code}")
        items = listing.json().get("ric:items", [])
    except NetworkRefused as exc:
        raise ConnectorError(f"requete refusee par le garde reseau : {exc}") from None
    except (ValueError, AttributeError) as exc:
        raise ConnectorError(f"reponse inattendue de l'API : {type(exc).__name__}") from None

    files: list[RemoteFile] = []
    for item in items[:limit]:
        item_id = str(item.get("@id", ""))
        slug = item_id.rstrip("/").rsplit("/", 1)[-1]
        if not slug:
            errors.append("notice sans identifiant ignoree")
            continue
        try:
            detail = guarded_request(
                "GET", f"{base}/records/{slug}", hosts, headers={"Accept": "application/ld+json, application/json"},
            )
            if not detail.ok:
                errors.append(f"{slug} : HTTP {detail.status_code}")
                continue
            markdown = record_to_markdown(detail.json(), fallback_title=slug)
        except NetworkRefused as exc:
            errors.append(f"{slug} : refuse par le garde reseau ({exc})")
            continue
        except ValueError:
            errors.append(f"{slug} : reponse non JSON")
            continue
        files.append(RemoteFile(
            filename=safe_filename(slug) + ".md", content=markdown.encode("utf-8"),
            content_type="text/markdown", source_id=item_id,
        ))
    return files, errors
