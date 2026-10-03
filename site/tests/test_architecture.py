"""EPIC-IAF-E19 US19.3 : regle d'architecture testee (cliquet). Les routes du site ne doivent pas ouvrir
elles-memes les magasins de graphe (Neo4j via `get_driver`) ni d'ontologies (Jena/Fuseki via `ontology`) :
cet acces appartient aux services (`app/services`), futurs agents d'ingestion, de structuration et
d'exposition (ADR 0009).

Etat mesure le 2026-10-03 : 3 routeurs heritaient de l'ancien fonctionnement (documents : 24 usages de
`get_driver`, 47 requetes Cypher directes, 7 appels `ontology` ; ask : 2 ; admin : 2 + 2). `ask` a ete migre le
2026-10-03 (il appelle le service d'exposition) : il reste documents et admin. La liste ci-dessous
ne peut que RETRECIR : un nouveau routeur qui ouvre les magasins fait echouer le premier test, un routeur
migre qui reste dans la liste fait echouer le second (il faut alors l'en retirer)."""
from __future__ import annotations

import ast
from pathlib import Path

ROUTERS = Path(__file__).resolve().parent.parent / "app" / "routers"
STORE_NAMES = {"get_driver", "ontology"}
LEGACY_STORE_ACCESS = {"admin.py", "documents.py"}


def _opens_the_stores(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] in STORE_NAMES:
                return True
            if any(alias.name in STORE_NAMES for alias in node.names):
                return True
        elif isinstance(node, ast.Import) and any(alias.name.split(".")[-1] in STORE_NAMES for alias in node.names):
            return True
    return False


def _offenders() -> set[str]:
    return {p.name for p in ROUTERS.glob("*.py") if p.name != "__init__.py" and _opens_the_stores(p)}


def test_new_routes_do_not_open_the_graph_or_ontology_stores():
    new_offenders = _offenders() - LEGACY_STORE_ACCESS
    assert not new_offenders, (
        f"{sorted(new_offenders)} ouvrent directement Neo4j ou Jena : passer par app/services (ADR 0009)"
    )


def test_migrated_routes_leave_the_legacy_list():
    migrated = LEGACY_STORE_ACCESS - _offenders()
    assert not migrated, f"{sorted(migrated)} n'accedent plus aux magasins : les retirer de LEGACY_STORE_ACCESS"


def test_the_knowledge_and_ask_pages_read_only_through_the_exposition_service():
    for name in ("knowledge.py", "ask.py"):
        source = (ROUTERS / name).read_text(encoding="utf-8")
        assert "exposition_client as exposition" in source and "session.run" not in source and "chat(" not in source
