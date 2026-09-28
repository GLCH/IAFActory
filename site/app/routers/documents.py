"""US3.1 (deposer), US3.2 (ingerer), US7.4/US7.5 (reconnaissance, classe
provisoire), US3.9 (voir les classes). Premiere version reelle, deliberement
simplifiee : voir docs/epics/EPIC-IAF-E3-graph-rag.md et pipeline.py pour ce
qui est simplifie par rapport a l'epic complet."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import require_role
from ..graph import get_driver
from ..models import Document, DocumentStatus, Role, User
from ..pdf_struct import ScannedDocument
from ..pipeline import UnsupportedFormat, ingest_document

router = APIRouter(prefix="/creator", dependencies=[Depends(require_role(Role.creator))])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

# US3.1 : formats acceptes par le depot ; les trois sont reellement analyses
# (docx_struct.py, pdf_struct.py, pptx_struct.py - qualite tres inegale,
# voir pipeline.py).
ACCEPTED_SUFFIXES = {".docx", ".pdf", ".pptx"}
MAX_SIZE_BYTES = 20 * 1024 * 1024  # 20 Mo, provisoire (US3.1 : "taille max configurable")


@router.get("/documents/new")
def new_document_form(request: Request, user: User = Depends(require_role(Role.creator))):
    return templates.TemplateResponse(request, "creator_document_new.html", {"error": None, "user": user})


def _run_pipeline_and_update(doc: Document, stored_path: Path, db: Session) -> None:
    """Fait tourner le pipeline pour un `Document` deja stocke sur le volume
    et reporte le resultat sur la ligne Postgres. Partage entre le depot
    initial et le nouveau tentative (US3.4 : pas de silence sur un echec ;
    utilise aussi pour recuperer une ligne restee bloquee `received` apres un
    arret du serveur en cours de traitement - constate reellement le
    2026-09-27 lors du developpement de cette fonctionnalite)."""
    try:
        result = ingest_document(doc.sha256, doc.filename, stored_path)
    except (UnsupportedFormat, ScannedDocument) as exc:
        doc.status = DocumentStatus.error
        doc.error_message = str(exc)
    except Exception as exc:  # pas de silence (regle du projet) : la cause reelle est conservee
        doc.status = DocumentStatus.error
        doc.error_message = f"{type(exc).__name__}: {exc}"[:2000]
    else:
        doc.status = result.status
        doc.neo4j_class_id = result.neo4j_class_id
        doc.class_name = result.class_name
        doc.chunk_count = result.chunk_count
        doc.entity_count = result.entity_count
        doc.error_message = "; ".join(result.warnings) if result.warnings else None
        doc.ingested_at = datetime.now(timezone.utc)
    db.commit()


@router.post("/documents/new")
async def new_document_submit(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.creator)),
):
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    ctx = {"error": None, "user": user}

    if suffix not in ACCEPTED_SUFFIXES:
        ctx["error"] = f"format non accepte ({suffix or 'sans extension'}) : .docx, .pdf ou .pptx seulement"
        return templates.TemplateResponse(request, "creator_document_new.html", ctx, status_code=422)

    raw = await file.read()
    if not raw:
        ctx["error"] = "fichier vide"
        return templates.TemplateResponse(request, "creator_document_new.html", ctx, status_code=422)
    if len(raw) > MAX_SIZE_BYTES:
        ctx["error"] = f"fichier trop volumineux ({len(raw) // 1024} Ko, maximum {MAX_SIZE_BYTES // 1024} Ko)"
        return templates.TemplateResponse(request, "creator_document_new.html", ctx, status_code=422)

    sha256 = hashlib.sha256(raw).hexdigest()
    if db.scalar(select(Document).where(Document.sha256 == sha256)) is not None:
        # US3.1 : "un meme contenu redepose n'est pas duplique".
        ctx["error"] = "ce document (meme contenu) est deja present"
        return templates.TemplateResponse(request, "creator_document_new.html", ctx, status_code=409)

    settings.documents_dir.mkdir(parents=True, exist_ok=True)
    stored_path = settings.documents_dir / f"{sha256}{suffix}"
    stored_path.write_bytes(raw)

    doc = Document(
        creator_id=user.id,
        filename=filename,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(raw),
        sha256=sha256,
        status=DocumentStatus.received,
    )
    db.add(doc)
    db.commit()

    _run_pipeline_and_update(doc, stored_path, db)

    return RedirectResponse("/creator/documents", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/documents/{document_id}/retry")
def retry_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.creator)),
):
    """Relance le pipeline pour un document reste bloque `received` (serveur
    arrete en cours de traitement - pas de file de taches persistante, ADR
    0006 non fait) ou en `error`. Le fichier source reste sur le volume
    (indexe par sha256), aucun nouveau depot necessaire."""
    doc = db.get(Document, document_id)
    if doc is None:
        return RedirectResponse("/creator/documents", status_code=status.HTTP_303_SEE_OTHER)
    suffix = Path(doc.filename).suffix.lower()
    stored_path = settings.documents_dir / f"{doc.sha256}{suffix}"
    if not stored_path.exists():
        doc.status = DocumentStatus.error
        doc.error_message = "fichier source introuvable sur le volume : redeposer le document"
        db.commit()
    else:
        _run_pipeline_and_update(doc, stored_path, db)
    return RedirectResponse("/creator/documents", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/documents")
def list_documents(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.creator))):
    documents = list(db.scalars(select(Document).order_by(Document.created_at.desc())))
    return templates.TemplateResponse(request, "creator_documents.html", {"documents": documents, "user": user})


@router.get("/classes")
def list_classes(request: Request, user: User = Depends(require_role(Role.creator))):
    driver = get_driver()
    with driver.session() as session:
        rows = list(session.run(
            "MATCH (c:DocumentClass) "
            "OPTIONAL MATCH (c)<-[:IN_CLASS]-(d:Document) "
            "RETURN c.id AS id, c.name AS name, c.status AS status, c.created_at AS created_at, "
            "       count(DISTINCT d) AS document_count "
            "ORDER BY c.created_at DESC"
        ))
    classes = [dict(row) for row in rows]
    return templates.TemplateResponse(request, "creator_classes.html", {"classes": classes, "user": user})


@router.get("/classes/{class_id}")
def class_detail(request: Request, class_id: str, user: User = Depends(require_role(Role.creator))):
    driver = get_driver()
    with driver.session() as session:
        head = session.run(
            "MATCH (c:DocumentClass {id: $cid}) RETURN c.name AS name, c.status AS status", cid=class_id,
        ).single()
        if head is None:
            return templates.TemplateResponse(
                request, "creator_class_detail.html",
                {
                    "class_id": class_id, "name": None, "status": None, "documents": [], "entity_types": [],
                    "relations": [], "concepts": [], "profile": None, "threshold": settings.recognition_threshold,
                    "user": user,
                },
                status_code=404,
            )
        documents = list(session.run(
            "MATCH (c:DocumentClass {id: $cid})<-[r:IN_CLASS]-(d:Document) "
            "RETURN d.filename AS filename, d.sha256 AS sha256, r.score AS score, "
            "       r.structural_score AS structural_score, r.semantic_score AS semantic_score, r.method AS method "
            "ORDER BY d.ingested_at DESC",
            cid=class_id,
        ))
        # US3.14 : ontologie structurelle (profil moyen des documents membres,
        # US3.11) et ontologie semantique (concepts OWL induits, US7.5 -
        # ecrits reellement dans Fuseki par ontology.py depuis le 2026-09-28,
        # relus ici depuis leur miroir Neo4j pour la vitesse d'affichage).
        profile_row = session.run(
            "MATCH (c:DocumentClass {id: $cid})<-[:IN_CLASS]-(d:Document) "
            "RETURN avg(d.profile_section) AS section, avg(d.profile_paragraph) AS paragraph, "
            "       avg(d.profile_table) AS table",
            cid=class_id,
        ).single()
        # avg() renvoie null si aucun document membre n'a de profil (documents
        # ingeres avant l'ajout du profil structurel au pipeline, 2026-09-28) -
        # verifier le champ lui-meme, pas seulement le nombre de documents.
        profile = None
        if profile_row and profile_row["section"] is not None:
            profile = {"section": profile_row["section"], "paragraph": profile_row["paragraph"], "table": profile_row["table"]}
        concepts = list(session.run(
            "MATCH (c:DocumentClass {id: $cid})-[:HAS_CONCEPT]->(concept:Concept) "
            "OPTIONAL MATCH (:Document)-[m:MENTIONS_CONCEPT]->(concept) "
            "RETURN concept.label AS label, concept.uri AS uri, collect(DISTINCT m.term) AS terms "
            "ORDER BY label",
            cid=class_id,
        ))
        # Entites/relations par chunk (extraction existante, distincte du
        # vocabulaire/concepts ci-dessus - voir pipeline.py).
        entity_types = list(session.run(
            "MATCH (e:Entity {class_id: $cid}) "
            "RETURN e.type AS type, count(e) AS entity_count "
            "ORDER BY entity_count DESC",
            cid=class_id,
        ))
        relations = list(session.run(
            "MATCH (:Entity {class_id: $cid})-[r:REL]->(:Entity {class_id: $cid}) "
            "RETURN r.type AS type, count(r) AS relation_count "
            "ORDER BY relation_count DESC",
            cid=class_id,
        ))
    return templates.TemplateResponse(
        request, "creator_class_detail.html",
        {
            "class_id": class_id,
            "name": head["name"],
            "status": head["status"],
            "documents": documents,
            "entity_types": entity_types,
            "relations": relations,
            "concepts": concepts,
            "profile": profile,
            "threshold": settings.recognition_threshold,
            "user": user,
        },
    )
