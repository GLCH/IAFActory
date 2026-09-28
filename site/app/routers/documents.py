"""US3.1 (deposer, y compris repertoire/LaTeX/Markdown - US3.15), US3.2
(ingerer, desormais asynchrone - IAF-E13), US7.4/US7.5 (reconnaissance,
classe provisoire), US3.9/US3.14 (voir les classes), US3.16 (detail d'un
document), US3.17 (corpus et taxonomie). Voir docs/epics/EPIC-IAF-E3-graph-rag.md
et pipeline.py pour ce qui reste simplifie par rapport aux epics complets."""
from __future__ import annotations

import hashlib
import math
import uuid
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import requests
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import worker
from ..config import settings
from ..db import get_db
from ..deps import require_role
from ..graph import get_driver
from ..models import Document, DocumentStatus, PipelineRun, Role, User
from ..ontology import class_graph_uri, set_concept_metadata

router = APIRouter(prefix="/creator", dependencies=[Depends(require_role(Role.creator))])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

# US3.1/US3.15 : formats acceptes par le depot ; tous reellement analyses
# (docx_struct.py, pdf_struct.py, pptx_struct.py, markdown_struct.py,
# latex_struct.py - qualite tres inegale, voir pipeline.py).
ACCEPTED_SUFFIXES = {".docx", ".pdf", ".pptx", ".md", ".tex"}
MAX_SIZE_BYTES = 20 * 1024 * 1024  # 20 Mo, provisoire (US3.1 : "taille max configurable")


def _save_and_enqueue(raw: bytes, filename: str, content_type: str, user: User, db: Session) -> str | None:
    """Enregistre un fichier depose et met son ingestion en file (IAF-E13).
    Renvoie un message d'erreur (et ne fait rien d'autre) si le fichier est
    invalide, sinon None. Partagee par le depot unique et le depot en lot
    (US3.15) pour ne pas dupliquer les regles de validation."""
    suffix = Path(filename).suffix.lower()
    if suffix not in ACCEPTED_SUFFIXES:
        accepted = ", ".join(sorted(ACCEPTED_SUFFIXES))
        return f"format non accepte ({suffix or 'sans extension'}) : {accepted} seulement"
    if not raw:
        return "fichier vide"
    if len(raw) > MAX_SIZE_BYTES:
        return f"fichier trop volumineux ({len(raw) // 1024} Ko, maximum {MAX_SIZE_BYTES // 1024} Ko)"

    sha256 = hashlib.sha256(raw).hexdigest()
    if db.scalar(select(Document).where(Document.sha256 == sha256)) is not None:
        return "ce document (meme contenu) est deja present"  # US3.1 : pas de doublon

    settings.documents_dir.mkdir(parents=True, exist_ok=True)
    stored_path = settings.documents_dir / f"{sha256}{suffix}"
    stored_path.write_bytes(raw)

    doc = Document(
        creator_id=user.id,
        filename=filename,
        content_type=content_type or "application/octet-stream",
        size_bytes=len(raw),
        sha256=sha256,
        status=DocumentStatus.received,
    )
    db.add(doc)
    db.commit()

    worker.enqueue(doc.id, stored_path)  # IAF-E13 : ne bloque pas la requete HTTP
    return None


@router.get("/documents/new")
def new_document_form(request: Request, user: User = Depends(require_role(Role.creator))):
    return templates.TemplateResponse(request, "creator_document_new.html", {"error": None, "user": user})


@router.post("/documents/new")
async def new_document_submit(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.creator)),
):
    raw = await file.read()
    error = _save_and_enqueue(raw, file.filename or "", file.content_type or "", user, db)
    if error:
        status_code = 409 if "deja present" in error else 422
        return templates.TemplateResponse(
            request, "creator_document_new.html", {"error": error, "user": user}, status_code=status_code,
        )
    return RedirectResponse("/creator/documents", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/documents/bulk")
async def bulk_document_submit(
    request: Request,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.creator)),
):
    """US3.15 : un repertoire complet ou plusieurs fichiers a la fois. Chaque
    fichier est independant : un format refuse ou un doublon dans le lot ne
    bloque pas les autres."""
    accepted = 0
    errors: list[str] = []
    for file in files:
        raw = await file.read()
        error = _save_and_enqueue(raw, file.filename or "", file.content_type or "", user, db)
        if error:
            errors.append(f"{file.filename} : {error}")
        else:
            accepted += 1

    summary = f"{accepted} document(s) mis en file"
    if errors:
        summary += f" ; {len(errors)} ecarte(s) : " + " | ".join(errors[:10])
    return templates.TemplateResponse(
        request, "creator_documents_bulk_result.html", {"summary": summary, "errors": errors, "user": user},
    )


@router.post("/documents/{document_id}/retry")
def retry_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.creator)),
):
    """Relance le pipeline (en file, IAF-E13) pour un document reste bloque
    `received` ou en `error`. Le fichier source reste sur le volume (indexe
    par sha256), aucun nouveau depot necessaire."""
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
        worker.enqueue(doc.id, stored_path)
    return RedirectResponse("/creator/documents", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/documents")
def list_documents(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.creator))):
    documents = list(db.scalars(select(Document).order_by(Document.created_at.desc())))
    return templates.TemplateResponse(request, "creator_documents.html", {"documents": documents, "user": user})


@router.get("/processes")
def list_processes(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.creator))):
    """IAF-E13 US13.7 : suivi des executions asynchrones du pipeline."""
    rows = db.execute(
        select(PipelineRun, Document.filename)
        .join(Document, PipelineRun.document_id == Document.id)
        .order_by(PipelineRun.created_at.desc())
        .limit(200)
    ).all()
    runs = [{"run": run, "filename": filename} for run, filename in rows]
    return templates.TemplateResponse(request, "creator_processes.html", {"runs": runs, "user": user})


@router.get("/documents/{document_id}")
def document_detail(
    request: Request, document_id: uuid.UUID, db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.creator)),
):
    """US3.16 : detail d'un document - chunks et graphe de connaissance issus
    de ce document precis (pas de la classe entiere)."""
    doc = db.get(Document, document_id)
    if doc is None:
        return templates.TemplateResponse(
            request, "creator_document_detail.html",
            {"document": None, "chunks": [], "graph_svg": None, "user": user},
            status_code=404,
        )

    chunks: list[dict] = []
    nodes: dict[str, str] = {}
    edges: list[tuple[str, str, str]] = []
    driver = get_driver()
    with driver.session() as session:
        chunk_rows = session.run(
            "MATCH (d:Document {sha256: $sha256})-[:HAS_ELEMENT]->(:StructElement)-[:CHILD*0..]->(el:StructElement)-[:HAS_CHUNK]->(c:Chunk) "
            "OPTIONAL MATCH (c)-[:MENTIONS]->(e:Entity) "
            "RETURN el.label AS section, el.kind AS kind, el.position AS position, c.text AS text, "
            "       collect(DISTINCT e.name) AS entities "
            "ORDER BY el.position",
            sha256=doc.sha256,
        )
        for row in chunk_rows:
            chunks.append({
                "section": row["section"], "kind": row["kind"], "text": row["text"],
                "entities": [e for e in row["entities"] if e],
            })

        entity_rows = session.run(
            "MATCH (d:Document {sha256: $sha256})-[:HAS_ELEMENT]->(:StructElement)-[:CHILD*0..]->(:StructElement)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e:Entity) "
            "RETURN DISTINCT e.name AS name, e.type AS type",
            sha256=doc.sha256,
        )
        for row in entity_rows:
            nodes[row["name"]] = row["type"] or "Autre"

        if nodes:
            rel_rows = session.run(
                "MATCH (d:Document {sha256: $sha256})-[:HAS_ELEMENT]->(:StructElement)-[:CHILD*0..]->(:StructElement)-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(s:Entity) "
                "MATCH (s)-[r:REL]->(t:Entity) WHERE t.name IN $names "
                "RETURN DISTINCT s.name AS source, r.type AS type, t.name AS target",
                sha256=doc.sha256, names=list(nodes.keys()),
            )
            for row in rel_rows:
                edges.append((row["source"], row["type"], row["target"]))

    graph_svg = _render_graph_svg(nodes, edges) if nodes else None
    return templates.TemplateResponse(
        request, "creator_document_detail.html",
        {"document": doc, "chunks": chunks, "graph_svg": graph_svg, "user": user},
    )


@router.get("/documents/{document_id}/download")
def download_document(document_id: uuid.UUID, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if doc is None:
        return Response(status_code=404)
    suffix = Path(doc.filename).suffix.lower()
    stored_path = settings.documents_dir / f"{doc.sha256}{suffix}"
    if not stored_path.exists():
        return Response("fichier introuvable sur le volume", status_code=404)
    return Response(
        content=stored_path.read_bytes(),
        media_type=doc.content_type,
        headers={"Content-Disposition": f'attachment; filename="{doc.filename}"'},
    )


def _render_graph_svg(nodes: dict[str, str], edges: list[tuple[str, str, str]], size: int = 640) -> str:
    """US3.16 : graphe de connaissance sans bibliotheque JS externe (coherent
    avec le reste du site, aucune etape de build). Disposition en cercle -
    lisible jusqu'a une trentaine de noeuds, pas une mise en page a ressorts
    (force-directed) : simplification assumee."""
    center = size / 2
    radius = size / 2 - 90
    names = list(nodes.keys())
    positions: dict[str, tuple[float, float]] = {}
    for i, name in enumerate(names):
        angle = 2 * math.pi * i / max(len(names), 1)
        positions[name] = (center + radius * math.cos(angle), center + radius * math.sin(angle))

    parts = [f'<svg viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">']
    for src, rel, tgt in edges:
        if src not in positions or tgt not in positions:
            continue
        x1, y1 = positions[src]
        x2, y2 = positions[tgt]
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#cbd0d8" stroke-width="1.5" />')
        parts.append(f'<text x="{mx:.1f}" y="{my:.1f}" font-size="10" fill="#667085">{xml_escape(rel or "")}</text>')
    for name, (x, y) in positions.items():
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="26" fill="#eef2ff" stroke="#4f46e5" stroke-width="1.5" />')
        label = name if len(name) <= 14 else name[:13] + "…"
        parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="10" text-anchor="middle" '
            f'dominant-baseline="middle" fill="#1a1d23">{xml_escape(label)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


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


@router.get("/classes/{class_id}/ontology.owl")
def class_ontology_owl(class_id: str):
    """US3.16 : export RDF/XML de l'ontologie semantique de la classe,
    recupere directement depuis Fuseki (source de verite, ADR 0001) - pas
    regenere depuis Neo4j."""
    graph = class_graph_uri(class_id)
    r = requests.get(
        f"{settings.fuseki_url}/{settings.fuseki_dataset}/data",
        params={"graph": graph},
        headers={"Accept": "application/rdf+xml"},
        timeout=15,
    )
    if r.status_code == 404:
        return Response("aucune ontologie pour cette classe", status_code=404)
    r.raise_for_status()
    return Response(
        content=r.content, media_type="application/rdf+xml",
        headers={"Content-Disposition": f'attachment; filename="classe-{class_id}.owl"'},
    )


@router.get("/corpus")
def list_corpus(request: Request, user: User = Depends(require_role(Role.creator))):
    """US3.17 : vue d'ensemble des corpus - meme donnees que /creator/classes,
    presentees comme point d'entree "corpus documentaires"."""
    driver = get_driver()
    with driver.session() as session:
        rows = list(session.run(
            "MATCH (c:DocumentClass) "
            "OPTIONAL MATCH (c)<-[:IN_CLASS]-(d:Document) "
            "RETURN c.id AS id, c.name AS name, c.status AS status, "
            "       count(DISTINCT d) AS document_count "
            "ORDER BY document_count DESC"
        ))
    corpus = [dict(row) for row in rows]
    return templates.TemplateResponse(request, "creator_corpus.html", {"corpus": corpus, "user": user})


@router.get("/taxonomy")
def taxonomy(request: Request, user: User = Depends(require_role(Role.creator))):
    """US3.17 : tous les concepts semantiques induits, toutes classes
    confondues, avec definition et traduction editables."""
    driver = get_driver()
    with driver.session() as session:
        rows = list(session.run(
            "MATCH (c:DocumentClass)-[:HAS_CONCEPT]->(concept:Concept) "
            "RETURN c.id AS class_id, c.name AS class_name, concept.label AS label, "
            "       coalesce(concept.definition, '') AS definition, "
            "       coalesce(concept.translation_en, '') AS translation_en "
            "ORDER BY concept.label"
        ))
    concepts = [dict(row) for row in rows]
    return templates.TemplateResponse(request, "creator_taxonomy.html", {"concepts": concepts, "user": user})


@router.post("/taxonomy/edit")
def taxonomy_edit(
    class_id: str = Form(...),
    label: str = Form(...),
    definition: str = Form(""),
    translation_en: str = Form(""),
    user: User = Depends(require_role(Role.creator)),
):
    """US3.17 : definition et traduction jamais inventees par le LLM - saisies
    par le creator, ecrites dans Fuseki (source de verite) et miroitees dans
    Neo4j (lecture rapide, meme principe que le reste des concepts)."""
    set_concept_metadata(class_id, label, definition.strip(), translation_en.strip())
    driver = get_driver()
    with driver.session() as session:
        session.run(
            "MATCH (c:DocumentClass {id: $cid})-[:HAS_CONCEPT]->(concept:Concept {label: $label}) "
            "SET concept.definition = $definition, concept.translation_en = $translation_en",
            cid=class_id, label=label, definition=definition.strip(), translation_en=translation_en.strip(),
        )
    return RedirectResponse("/creator/taxonomy", status_code=status.HTTP_303_SEE_OTHER)
