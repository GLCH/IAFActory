"""Archivage complet du service (admin uniquement, demande explicite
2026-09-29 : "ajoute une fonctionnalite pour l'admin de tout archiver").

Un seul fichier .zip : documents originaux (volume), export Neo4j (JSON,
noeuds+relations - approche manuelle via le driver Python, pas une procedure
APOC d'export dont la signature exacte n'est pas verifiee ici, coherent avec
la regle du projet de ne jamais deviner une API), export Fuseki (un .ttl par
graphe nomme), export Postgres (JSON des tables documents/pipeline_runs/
pipeline_steps/users - JAMAIS le mot de passe hashe, par prudence meme s'il
n'est pas reversible).

Pas encore de restauration (import) - archive en LECTURE SEULE pour
l'instant, a faire si le besoin de restaurer se confirme. Construite en
memoire (echelle dev de ce service, pas de flux/streaming - simplification
assumee, a revoir si le volume de documents grandit beaucoup)."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ontology
from .config import settings
from .graph import get_driver
from .models import Document, PipelineRun, PipelineStep, User


def _export_neo4j() -> bytes:
    driver = get_driver()
    nodes = []
    edges = []
    with driver.session() as session:
        for record in session.run("MATCH (n) RETURN n"):
            node = record["n"]
            nodes.append({"id": node.element_id, "labels": list(node.labels), "properties": dict(node.items())})
        for record in session.run("MATCH ()-[r]->() RETURN r"):
            rel = record["r"]
            edges.append({
                "type": rel.type, "start": rel.start_node.element_id, "end": rel.end_node.element_id,
                "properties": dict(rel.items()),
            })
    return json.dumps({"nodes": nodes, "relationships": edges}, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def _export_postgres(db: Session) -> bytes:
    data = {
        "documents": [
            {
                "id": str(d.id), "filename": d.filename, "sha256": d.sha256, "status": d.status.value,
                "neo4j_class_id": d.neo4j_class_id, "class_name": d.class_name,
                "chunk_count": d.chunk_count, "entity_count": d.entity_count,
                "created_at": d.created_at.isoformat(),
                "ingested_at": d.ingested_at.isoformat() if d.ingested_at else None,
            }
            for d in db.scalars(select(Document))
        ],
        "pipeline_runs": [
            {
                "id": str(r.id), "document_id": str(r.document_id), "status": r.status.value,
                "created_at": r.created_at.isoformat(),
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in db.scalars(select(PipelineRun))
        ],
        "pipeline_steps": [
            {
                "id": str(s.id), "run_id": str(s.run_id), "phase": s.phase, "label": s.label,
                "detail": s.detail, "at": s.at.isoformat(),
            }
            for s in db.scalars(select(PipelineStep))
        ],
        "users": [{"id": str(u.id), "email": u.email, "role": u.role.value} for u in db.scalars(select(User))],
    }
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def build_archive(db: Session) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("neo4j/graph.json", _export_neo4j())
        zf.writestr("postgres/tables.json", _export_postgres(db))
        try:
            for graph_uri in ontology.list_graphs():
                name = graph_uri.rsplit("/", 1)[-1].replace(":", "_") or "graphe"
                try:
                    zf.writestr(f"fuseki/{name}.ttl", ontology.export_graph_turtle(graph_uri))
                except Exception as exc:
                    zf.writestr(f"fuseki/{name}.ERREUR.txt", str(exc))
        except Exception as exc:
            zf.writestr("fuseki/ERREUR.txt", str(exc))
        if settings.documents_dir.exists():
            for path in settings.documents_dir.iterdir():
                if path.is_file():
                    zf.write(path, f"documents/{path.name}")
        zf.writestr("MANIFEST.txt", f"Archive IAFActory generee le {datetime.now(timezone.utc).isoformat()}\n")
    return buffer.getvalue()
