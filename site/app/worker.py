"""File d'execution asynchrone du pipeline (IAF-E13 US13.1/US13.7), demandee
explicitement le 2026-09-28 : "tout process long doit etre asynchrone".

Version simplifiee, assumee : un `ThreadPoolExecutor` a l'interieur du
process uvicorn, pas un service separe (l'ADR 0006 proposait une file
Postgres derriere une interface `StageRunner`, options B/C - moteur de
workflow, courtier de messages - non retenues). Chaque execution a sa PROPRE
session SQLAlchemy (une session n'est pas partageable entre threads) et son
propre driver Neo4j (deja un singleton thread-safe, graph.py).

Limite assumee et non cachee : un `PipelineRun` `running` au moment d'un
redemarrage du serveur reste bloque dans cet etat - pas de reprise
automatique (US13.6 reste a faire)."""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import class_lifecycle, class_merge
from .db import SessionLocal
from .models import Document, DocumentStatus, PipelineRun, PipelineRunStatus, PipelineStep
from .pdf_struct import ScannedDocument
from .pipeline import UnsupportedFormat, ingest_document

# Concurrence bornee (IAF-E13 US13.8, version minimale) : la passerelle LLM
# et Neo4j supportent plusieurs appels en parallele, mais un modele local
# (Ollama) degraderait fortement au-dela de quelques executions simultanees.
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pipeline")


def enqueue(document_id: uuid.UUID, stored_path: Path) -> uuid.UUID:
    """Met un document en file et rend la main immediatement (US13.7) ;
    l'appelant HTTP ne bloque plus le temps du pipeline complet."""
    db = SessionLocal()
    try:
        run = PipelineRun(document_id=document_id, status=PipelineRunStatus.queued)
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    _executor.submit(_execute, run_id, document_id, stored_path)
    return run_id


def _execute(run_id: uuid.UUID, document_id: uuid.UUID, stored_path: Path) -> None:
    db = SessionLocal()
    class_id_to_check: str | None = None  # reste None si retour anticipe ou si aucune classe assignee
    try:
        run = db.get(PipelineRun, run_id)
        doc = db.get(Document, document_id)
        if run is None or doc is None:
            return  # ligne supprimee entre-temps : rien a faire

        run.status = PipelineRunStatus.running
        run.started_at = datetime.now(timezone.utc)
        db.commit()

        # Ajoute le 2026-09-28 : trace chaque etape reelle du pipeline
        # (demande explicite de detail par etape/agent sur /creator/processes).
        # Commit immediat par etape (pas dans la transaction principale) pour
        # que la progression soit visible EN COURS d'execution, pas seulement
        # a la fin - c'est tout l'interet par rapport au statut global existant.
        def on_step(phase: str, label: str, detail: str | None) -> None:
            db.add(PipelineStep(run_id=run_id, phase=phase, label=label, detail=detail))
            db.commit()

        try:
            result = ingest_document(doc.sha256, doc.filename, stored_path, on_step=on_step)
        except (UnsupportedFormat, ScannedDocument) as exc:
            doc.status = DocumentStatus.error
            doc.error_message = str(exc)
            run.status = PipelineRunStatus.error
            run.error_message = str(exc)
        except Exception as exc:  # pas de silence (regle du projet) : la cause reelle est conservee
            message = f"{type(exc).__name__}: {exc}"[:2000]
            doc.status = DocumentStatus.error
            doc.error_message = message
            run.status = PipelineRunStatus.error
            run.error_message = message
        else:
            doc.status = result.status
            doc.neo4j_class_id = result.neo4j_class_id
            doc.class_name = result.class_name
            doc.chunk_count = result.chunk_count
            doc.entity_count = result.entity_count
            doc.error_message = "; ".join(result.warnings) if result.warnings else None
            doc.ingested_at = datetime.now(timezone.utc)
            run.status = PipelineRunStatus.done

        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        class_id_to_check = doc.neo4j_class_id
    finally:
        db.close()

    # IAF-E7 US7.6 (etendue) : distance entre classes, hors de la transaction
    # ci-dessus (sa propre session Postgres/Neo4j) - un echec ici ne doit pas
    # invalider le document deja ingere.
    if class_id_to_check:
        # EPIC-IAF-E17 : classe PROVISOIRE -> fusion par densite de similarite
        # puis promotion (class_lifecycle) ; classe OFFICIELLE -> US7.6 inchange.
        try:
            class_lifecycle.run_after_ingest(class_id_to_check, run_id)
        except Exception:
            pass
        try:
            class_merge.check_and_act_on_class(class_id_to_check)
        except Exception:
            pass
