"""US3.1 (deposer, y compris repertoire/LaTeX/Markdown - US3.15), US3.2
(ingerer, desormais asynchrone - IAF-E13), US7.4/US7.5 (reconnaissance,
classe provisoire), US3.9/US3.14 (voir les classes), US3.16 (detail d'un
document), US3.17 (corpus et taxonomie). Voir docs/epics/EPIC-IAF-E3-graph-rag.md
et pipeline.py pour ce qui reste simplifie par rapport aux epics complets."""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import class_lifecycle, class_merge, class_reduction, taxonomy_builder, type_reduction, worker
from ..config import settings
from ..db import get_db
from ..deps import require_role
from ..graph import get_driver
from ..models import Document, DocumentStatus, PipelineRun, PipelineStep, Role, User
from ..ontology import (
    class_graph_has_content,
    delete_class_ontology,
    delete_concept as ontology_delete_concept,
    delete_taxonomy as ontology_delete_taxonomy,
    describe_structural_ontologies,
    EXPORT_FORMATS,
    export_class_owl,
    export_structural_ontology,
    list_accepted_structures,
    normalize_export_format,
    export_taxonomy_turtle,
    set_concept_metadata,
)

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
    """IAF-E13 US13.7 : suivi des executions asynchrones du pipeline.
    Ajoute le 2026-09-28 : le detail par etape (PipelineStep) - "quelle etape
    a fait quelque chose (ou beaucoup)" sur l'ingestion et la structuration
    (vocabulaire du doc d'architecture semantique). Pas d'etape "Exposition"
    ici : elle a lieu au moment d'une question du viewer, pas a l'ingestion -
    voir models.py:PipelineStep."""
    rows = db.execute(
        select(PipelineRun, Document.filename)
        .join(Document, PipelineRun.document_id == Document.id)
        .order_by(PipelineRun.created_at.desc())
        .limit(200)
    ).all()
    run_ids = [run.id for run, _ in rows]
    steps_by_run: dict[uuid.UUID, list[PipelineStep]] = {rid: [] for rid in run_ids}
    if run_ids:
        for s in db.scalars(
            select(PipelineStep).where(PipelineStep.run_id.in_(run_ids)).order_by(PipelineStep.at)
        ):
            steps_by_run[s.run_id].append(s)
    runs = [{"run": run, "filename": filename, "steps": steps_by_run[run.id]} for run, filename in rows]
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
            {"document": None, "chunks": [], "graph_nodes": [], "graph_edges": [], "user": user},
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

    graph_nodes, graph_edges, graph_total_entities = _build_graph_data(nodes, edges) if nodes else ([], [], 0)
    return templates.TemplateResponse(
        request, "creator_document_detail.html",
        {
            "document": doc, "chunks": chunks, "user": user,
            "graph_nodes": graph_nodes, "graph_edges": graph_edges,
            "graph_total_entities": graph_total_entities, "graph_js_cdn": GRAPH_JS_CDN,
        },
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


def _delete_document_neo4j(session, sha256: str) -> None:
    """Supprime le contenu Neo4j PROPRE a un document (elements structurels,
    chunks) - jamais les Concept/Entity de sa classe, partages avec d'autres
    documents. Partagee par la suppression d'un document seul et la
    suppression en cascade d'une classe entiere ci-dessous."""
    session.run(
        "MATCH (d:Document {sha256: $sha256})-[:HAS_ELEMENT]->(:StructElement)-[:CHILD*0..]->(:StructElement)-[:HAS_CHUNK]->(c:Chunk) "
        "DETACH DELETE c",
        sha256=sha256,
    )
    session.run(
        "MATCH (d:Document {sha256: $sha256})-[:HAS_ELEMENT]->(top:StructElement)-[:CHILD*0..]->(el:StructElement) "
        "DETACH DELETE el",
        sha256=sha256,
    )
    session.run("MATCH (d:Document {sha256: $sha256}) DETACH DELETE d", sha256=sha256)


def _delete_document_postgres(doc: Document, db: Session) -> None:
    """Supprime la ligne Document, ses PipelineRun/PipelineStep (etrangere
    sans cascade declaree - ordre important) et le fichier sur le volume.
    N'appelle PAS db.commit() : l'appelant decide du regroupement transactionnel."""
    suffix = Path(doc.filename).suffix.lower()
    (settings.documents_dir / f"{doc.sha256}{suffix}").unlink(missing_ok=True)
    for run in db.scalars(select(PipelineRun).where(PipelineRun.document_id == doc.id)):
        for pipeline_step in db.scalars(select(PipelineStep).where(PipelineStep.run_id == run.id)):
            db.delete(pipeline_step)
        # flush() a CHAQUE niveau : aucune relationship() ORM n'est declaree
        # entre ces tables (seulement des ForeignKey() brutes), donc
        # SQLAlchemy ne peut pas deduire seul l'ordre correct des DELETE - bug
        # reel rencontre au premier test (2026-09-28), deux fois de suite
        # (pipeline_runs->documents, puis pipeline_steps->pipeline_runs) avant
        # de flusher a chaque etape plutot qu'une seule fois a la fin.
        db.flush()
        db.delete(run)
    db.flush()
    db.delete(doc)


@router.post("/documents/{document_id}/delete")
def delete_document(
    document_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_role(Role.creator)),
):
    """Ajoute le 2026-09-28 (gouvernance, demande explicite). Ne touche PAS
    aux Concept/Entity de sa classe : partages avec les AUTRES documents de la
    meme classe (ou simplement conserves comme trace de ce qui a ete appris,
    meme si ce document disparait) - limite assumee, comme class_merge.py ne
    migre pas les entites d'une classe fusionnee (US7.6)."""
    doc = db.get(Document, document_id)
    if doc is None:
        return RedirectResponse("/creator/documents", status_code=status.HTTP_303_SEE_OTHER)

    driver = get_driver()
    with driver.session() as session:
        _delete_document_neo4j(session, doc.sha256)
    _delete_document_postgres(doc, db)
    db.commit()
    return RedirectResponse("/creator/documents", status_code=status.HTTP_303_SEE_OTHER)


# US3.16, revise le 2026-09-29 (demande explicite : "ajoute une bibliotheque
# js pour pouvoir naviguer dans le graph") - remplace le rendu SVG statique
# (disposition en cercle, ecrite a la main) par vis-network (CDN cdnjs,
# version 10.1.2 verifiee via sa documentation, pas devinee), qui apporte
# zoom/glisser/deplacement des noeuds sans etape de build (un seul <script>).
# Plafond honnete a MAX_GRAPH_NODES : un document reel peut desormais avoir
# plusieurs centaines d'entites (US7.1/EXTRACTION_SYSTEM revises le
# 2026-09-29) - un graphe de 900+ noeuds resterait illisible et couteux a
# stabiliser cote navigateur meme avec une bibliotheque interactive ; garde
# les entites les plus connectees (degre), le reste visible via les chunks.
GRAPH_JS_CDN = "https://cdnjs.cloudflare.com/ajax/libs/vis-network/10.1.2/standalone/umd/vis-network.min.js"
MAX_GRAPH_NODES = 150


def _build_graph_data(
    nodes: dict[str, str], edges: list[tuple[str, str, str]],
) -> tuple[list[dict], list[dict], int]:
    """Renvoie (noeuds vis-network, arcs vis-network, nombre total d'entites
    AVANT plafonnement) - le gabarit affiche ce dernier si le graphe a ete
    reduit, pour ne jamais laisser croire que la vue est complete sans le dire."""
    total = len(nodes)
    if total > MAX_GRAPH_NODES:
        degree: dict[str, int] = {name: 0 for name in nodes}
        for src, _, tgt in edges:
            if src in degree:
                degree[src] += 1
            if tgt in degree:
                degree[tgt] += 1
        kept = set(sorted(nodes, key=lambda n: degree.get(n, 0), reverse=True)[:MAX_GRAPH_NODES])
        nodes = {n: t for n, t in nodes.items() if n in kept}
        edges = [(s, r, t) for s, r, t in edges if s in kept and t in kept]

    graph_nodes = [
        {"id": name, "label": name if len(name) <= 30 else name[:29] + "…", "title": name, "group": etype or "Autre"}
        for name, etype in nodes.items()
    ]
    graph_edges = [{"from": s, "to": t, "label": r or ""} for s, r, t in edges]
    return graph_nodes, graph_edges, total


@router.get("/classes")
def list_classes(request: Request, user: User = Depends(require_role(Role.creator))):
    driver = get_driver()
    with driver.session() as session:
        rows = list(session.run(
            "MATCH (c:DocumentClass) "
            "WHERE NOT coalesce(c.status, '') STARTS WITH 'fusionnee' "
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
                    "relations": [], "concepts": [], "structural_ontologies": [], "profile": None,
                    "threshold": settings.recognition_threshold, "user": user,
                },
                status_code=404,
            )
        if head["status"] and str(head["status"]).startswith("fusionnee_dans:"):
            # US7.6 (etendue) : URI coherente (demande explicite) - l'ancienne
            # classe redirige vers celle qui l'a absorbee plutot que 404.
            target_id = str(head["status"]).split(":", 1)[1]
            return RedirectResponse(f"/creator/classes/{target_id}", status_code=status.HTTP_302_FOUND)
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
            "       avg(d.profile_table) AS table, avg(d.profile_equation) AS equation, "
            "       avg(d.profile_citation_density) AS citation",
            cid=class_id,
        ).single()
        # avg() renvoie null si aucun document membre n'a de profil (documents
        # ingeres avant l'ajout du profil structurel au pipeline, 2026-09-28) -
        # verifier le champ lui-meme, pas seulement le nombre de documents.
        # "equation"/"citation" peuvent rester null si aucun document membre
        # n'a ce champ (documents plus anciens, ou format sans citations).
        profile = None
        if profile_row and profile_row["section"] is not None:
            profile = {
                "section": profile_row["section"], "paragraph": profile_row["paragraph"],
                "table": profile_row["table"], "equation": profile_row["equation"] or 0.0,
                "citation": profile_row["citation"] or 0.0,
            }
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
    try:
        structural_ontologies = describe_structural_ontologies(class_id)
    except Exception:
        structural_ontologies = []  # Fuseki indisponible : ne bloque pas l'ecran (meme principe que class_graph_has_content)
    with driver.session() as session:
        near_corpus = list(session.run(
            "MATCH (:DocumentClass {id: $cid})-[r:NEAR_CORPUS]->(o:DocumentClass) "
            "RETURN o.id AS id, o.name AS name, r.semantic_score AS score ORDER BY score DESC", cid=class_id,
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
            "structural_ontologies": structural_ontologies,
            "near_corpus": near_corpus,
            "document_count": len(documents),
            "official_min_documents": settings.official_class_min_documents,
            "profile": profile,
            "threshold": settings.recognition_threshold,
            "user": user,
        },
    )


@router.get("/classes/{class_id}/structure-ontology")
def class_structure_ontology_download(class_id: str, uri: str, fmt: str = "ttl"):
    """2026-10-02 (demande explicite) : telecharge, en Turtle, UNE des
    ontologies structurelles acceptees par la classe. `uri` n'est servie que
    si la classe l'accepte reellement (`iafs:acceptsStructure`) - jamais un
    export arbitraire d'un graphe Fuseki choisi par l'appelant."""
    try:
        accepted = list_accepted_structures(class_id)
        if uri not in accepted:
            return Response("ontologie structurelle non acceptee par cette classe", status_code=404)
        fmt = normalize_export_format(fmt)
        content = export_structural_ontology(uri, fmt)
    except Exception:
        return Response("Fuseki indisponible", status_code=503)
    if content is None:
        return Response("ontologie structurelle introuvable", status_code=404)
    filename = uri.rsplit(":", 1)[-1].replace("#", "-").replace("/", "-") or "ontologie-structurelle"
    media_type, extension = EXPORT_FORMATS[fmt]
    return Response(
        content=content, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}.{extension}"'},
    )


@router.get("/classes/{class_id}/ontology")
@router.get("/classes/{class_id}/ontology.owl")
def class_ontology_owl(class_id: str, fmt: str = "owl"):
    """US3.16 : export de l'ontologie semantique de la classe,
    recupere directement depuis Fuseki (source de verite, ADR 0001) - pas
    regenere depuis Neo4j. Fusionne desormais le graphe de la classe
    (concepts) et le graphe global des proprietes (relations/attributs,
    US7.9) - bug reel corrige le 2026-09-29, voir ontology.export_class_owl.
    2026-10-02 : format au choix (`fmt=ttl` ou `fmt=owl`, OWL/RDF-XML par
    defaut comme avant ; l'ancienne URL `.owl` reste valable)."""
    if not class_graph_has_content(class_id):
        return Response("aucune ontologie pour cette classe", status_code=404)
    fmt = normalize_export_format(fmt)
    content = export_class_owl(class_id, fmt)
    media_type, extension = EXPORT_FORMATS[fmt]
    return Response(
        content=content, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="classe-{class_id}.{extension}"'},
    )


@router.get("/classes/{class_id}/reduce-ontology")
def reduce_ontology_page(
    request: Request, class_id: str, min_support: int | None = None, applied: int | None = None,
    user: User = Depends(require_role(Role.creator)),
):
    """EPIC-IAF-E17 US17.5 : apercu de la reduction de l'ontologie (concepts
    specifiques a peu de documents) AVANT toute ecriture."""
    with get_driver().session() as session:
        head = session.run("MATCH (c:DocumentClass {id: $cid}) RETURN c.name AS name", cid=class_id).single()
        if head is None:
            return templates.TemplateResponse(
                request, "creator_reduce_ontology.html", {"class_id": class_id, "name": None, "user": user}, status_code=404,
            )
        plan = class_reduction.plan_reduction(session, class_id, min_support)
    return templates.TemplateResponse(
        request, "creator_reduce_ontology.html",
        {"class_id": class_id, "name": head["name"], "plan": plan, "applied": applied, "user": user},
    )


@router.post("/classes/{class_id}/reduce-ontology/apply")
def reduce_ontology_apply(class_id: str, min_support: int = Form(...), user: User = Depends(require_role(Role.creator))):
    with get_driver().session() as session:
        removed = class_reduction.apply_reduction(session, class_id, min_support)
    return RedirectResponse(
        f"/creator/classes/{class_id}/reduce-ontology?min_support={min_support}&applied={removed}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/classes/{class_id}/promote")
def promote_class_route(
    class_id: str, db: Session = Depends(get_db), user: User = Depends(require_role(Role.creator)),
):
    """EPIC-IAF-E17 US17.4 : promotion MANUELLE d'une classe provisoire (avant
    le seuil automatique de documents)."""
    with get_driver().session() as session:
        class_lifecycle.promote_class(session, db, class_id)
    return RedirectResponse(f"/creator/classes/{class_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/classes/{class_id}/ontology-graph")
def class_ontology_graph(request: Request, class_id: str, user: User = Depends(require_role(Role.creator))):
    """Ajoute le 2026-09-30, demande explicite : "on doit voir les types
    d'objets (class, object property, datatype property)". Graphe de SCHEMA
    (TBox) - distinct du graphe de connaissance par document (US3.16, qui
    montre des INSTANCES). Montre honnêtement une incoherence de l'ontologie
    actuelle plutot que de la masquer : les concepts du vocabulaire (US7.1,
    deja ecrits comme owl:Class) et les types d'entites observes par
    extraction (US3.4, Entity.type - jamais encore ecrits comme owl:Class,
    c'est justement ce que la page de reduction ci-apres doit corriger) sont
    DEUX vocabulaires distincts aujourd'hui - affiches avec des styles
    differents (pas fondus en un seul groupe) pour ne pas laisser croire
    qu'ils sont deja unifies."""
    driver = get_driver()
    with driver.session() as session:
        head = session.run("MATCH (c:DocumentClass {id: $cid}) RETURN c.name AS name", cid=class_id).single()
        if head is None:
            return templates.TemplateResponse(
                request, "creator_ontology_graph.html",
                {"class_id": class_id, "name": None, "graph_nodes": [], "graph_edges": [], "user": user},
                status_code=404,
            )
        concept_labels = [
            r["label"] for r in session.run(
                "MATCH (c:DocumentClass {id: $cid})-[:HAS_CONCEPT]->(concept:Concept) RETURN DISTINCT concept.label AS label",
                cid=class_id,
            ) if r["label"]
        ]
        entity_types_all = [
            (r["type"], r["n"]) for r in session.run(
                "MATCH (e:Entity {class_id: $cid}) RETURN DISTINCT e.type AS type, count(e) AS n", cid=class_id,
            ) if r["type"]
        ]
        rel_usage = list(session.run(
            "MATCH (s:Entity {class_id: $cid})-[r:REL]->(t:Entity {class_id: $cid}) "
            "RETURN DISTINCT s.type AS source_type, r.type AS rel_type, t.type AS target_type",
            cid=class_id,
        ))
        attr_usage = list(session.run(
            "MATCH (e:Entity {class_id: $cid}) "
            "UNWIND [k IN keys(e) WHERE NOT k IN ['name', 'type', 'class_id']] AS key "
            "RETURN DISTINCT e.type AS entity_type, key",
            cid=class_id,
        ))

    # Plafond honnete (meme principe que _build_graph_data, US3.16) : ce
    # graphe a revele reellement 89 types d'entites et 184 relations pour une
    # seule classe (Part I, test du 2026-09-30) - illisible sans limite.
    # Garde les types les plus FREQUENTS (les plus etablis), pas les plus
    # rares (la longue traine, justement ce qui doit etre reduit en priorite -
    # voir /reduce-types plutot que ce graphe pour ceux-la).
    entity_types_total = len(entity_types_all)
    entity_types = sorted(entity_types_all, key=lambda t: t[1], reverse=True)[:MAX_GRAPH_NODES]
    kept_type_names = {t for t, _ in entity_types}
    rel_usage = [
        r for r in rel_usage if r["source_type"] in kept_type_names and r["target_type"] in kept_type_names
    ]
    attr_usage = [r for r in attr_usage if r["entity_type"] in kept_type_names]

    graph_nodes = []
    graph_edges = []
    seen_ids: set[str] = set()

    def add_node(node_id: str, label: str, group: str, shape: str | None = None) -> None:
        if node_id in seen_ids:
            return
        seen_ids.add(node_id)
        node = {"id": node_id, "label": label, "title": label, "group": group}
        if shape:
            node["shape"] = shape
        graph_nodes.append(node)

    for label in concept_labels:
        add_node(f"class:{label}", label, "Class (vocabulaire, owl:Class)")
    for etype, n in entity_types:
        add_node(f"type:{etype}", f"{etype} ({n})", "Type d'entite (pas encore owl:Class)", shape="diamond")
    for row in rel_usage:
        src, rel, tgt = row["source_type"], row["rel_type"], row["target_type"]
        if not (src and rel and tgt):
            continue
        add_node(f"type:{src}", src, "Type d'entite (pas encore owl:Class)", shape="diamond")
        add_node(f"type:{tgt}", tgt, "Type d'entite (pas encore owl:Class)", shape="diamond")
        add_node(f"objprop:{rel}", rel, "owl:ObjectProperty", shape="hexagon")
        graph_edges.append({"from": f"type:{src}", "to": f"objprop:{rel}", "label": "domaine"})
        graph_edges.append({"from": f"objprop:{rel}", "to": f"type:{tgt}", "label": "portee"})
    for row in attr_usage:
        etype, key = row["entity_type"], row["key"]
        if not (etype and key):
            continue
        add_node(f"type:{etype}", etype, "Type d'entite (pas encore owl:Class)", shape="diamond")
        add_node(f"dataprop:{key}", key, "owl:DatatypeProperty", shape="square")
        graph_edges.append({"from": f"type:{etype}", "to": f"dataprop:{key}", "label": ""})

    return templates.TemplateResponse(
        request, "creator_ontology_graph.html",
        {
            "class_id": class_id, "name": head["name"], "graph_nodes": graph_nodes, "graph_edges": graph_edges,
            "graph_js_cdn": GRAPH_JS_CDN, "user": user,
            "entity_types_shown": len(entity_types), "entity_types_total": entity_types_total,
        },
    )


@router.get("/classes/{class_id}/reduce-types")
def reduce_types(request: Request, class_id: str, user: User = Depends(require_role(Role.creator))):
    """Ajoute le 2026-09-30, demande explicite : "trop de terme pour definir
    les classes, relations et attributs des ontologies [...] on doit bien
    distinguer les entites possibles des entites types". Analyse (liste +
    suggestions) ; l'application se fait via /reduce-types/preview puis
    /apply (decide avec l'utilisateur le 2026-09-30 : lot hierarchique avec
    apercu, pas de fusion paire par paire ni d'automatique silencieux)."""
    driver = get_driver()
    with driver.session() as session:
        head = session.run("MATCH (c:DocumentClass {id: $cid}) RETURN c.name AS name", cid=class_id).single()
        if head is None:
            return templates.TemplateResponse(
                request, "creator_reduce_types.html",
                {"class_id": class_id, "name": None, "entity_types": [], "suggestions": [], "user": user},
                status_code=404,
            )
        entity_types = type_reduction.list_entity_types(session, class_id)
    suggestions = type_reduction.suggest_type_merges(entity_types)
    return templates.TemplateResponse(
        request, "creator_reduce_types.html",
        {
            "class_id": class_id, "name": head["name"], "entity_types": entity_types,
            "suggestions": suggestions, "user": user,
            "applied": request.query_params.get("applied"), "n_entities": request.query_params.get("n_entities"),
        },
    )


@router.get("/classes/{class_id}/reduce-types/preview")
def reduce_types_preview(request: Request, class_id: str, user: User = Depends(require_role(Role.creator))):
    """Apercu du lot AVANT ecriture (demande explicite, "on en discute") :
    classification ascendante hierarchique complete (type_reduction.py),
    aucune ecriture tant que le creator n'a pas clique "Appliquer" ci-dessous."""
    driver = get_driver()
    with driver.session() as session:
        head = session.run("MATCH (c:DocumentClass {id: $cid}) RETURN c.name AS name", cid=class_id).single()
        if head is None:
            return RedirectResponse("/creator/classes", status_code=status.HTTP_303_SEE_OTHER)
        entity_types = type_reduction.list_entity_types(session, class_id)
    plan = type_reduction.build_reduction_plan(entity_types)
    changed_groups = [g for g in plan if len(g["members"]) > 1]
    unchanged_count = len(plan) - len(changed_groups)
    return templates.TemplateResponse(
        request, "creator_reduce_types_preview.html",
        {
            "class_id": class_id, "name": head["name"], "changed_groups": changed_groups,
            "unchanged_count": unchanged_count, "user": user,
        },
    )


@router.post("/classes/{class_id}/reduce-types/apply")
def reduce_types_apply(class_id: str, user: User = Depends(require_role(Role.creator))):
    """Applique le plan (decide avec l'utilisateur le 2026-09-30). Recalcule
    le plan cote serveur (pas de mapping transmis par le formulaire - un plan
    de 185 types serait volumineux a serialiser, et recalculer evite toute
    manipulation du plan entre l'apercu et l'application)."""
    driver = get_driver()
    with driver.session() as session:
        head = session.run("MATCH (c:DocumentClass {id: $cid}) RETURN c.name AS name", cid=class_id).single()
        if head is None:
            return RedirectResponse("/creator/classes", status_code=status.HTTP_303_SEE_OTHER)
        entity_types = type_reduction.list_entity_types(session, class_id)
        plan = type_reduction.build_reduction_plan(entity_types)
        n_types, n_entities = type_reduction.apply_reduction_plan(session, class_id, plan)
    return RedirectResponse(
        f"/creator/classes/{class_id}/reduce-types?applied={n_types}&n_entities={n_entities}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/classes/{class_id}/delete")
def delete_class(class_id: str, db: Session = Depends(get_db), user: User = Depends(require_role(Role.creator))):
    """Ajoute le 2026-09-28 (gouvernance, demande explicite) - "classe" et
    "corpus" sont le MEME objet Neo4j (DocumentClass, decide le 2026-09-27) :
    cette route sert les deux pages. Suppression en CASCADE (pas de refus si
    la classe a des documents) : plus utile pour purger les classes de test
    ou erronees que de forcer un vidage document par document - confirme par
    la demande elle-meme ("effacer un objet du service"). Supprime aussi les
    Concept/Entity propres a la classe (contrairement a la suppression d'un
    document seul ci-dessus) : ils ne sont PAS partages entre classes (chaque
    Concept est cle par (label, class_id), pipeline.py)."""
    driver = get_driver()
    with driver.session() as session:
        doc_shas = [
            r["sha256"] for r in session.run(
                "MATCH (c:DocumentClass {id: $cid})<-[:IN_CLASS]-(d:Document) RETURN d.sha256 AS sha256",
                cid=class_id,
            )
        ]
        for sha256 in doc_shas:
            _delete_document_neo4j(session, sha256)
        session.run(
            "MATCH (c:DocumentClass {id: $cid})-[:HAS_CONCEPT]->(concept:Concept) DETACH DELETE concept",
            cid=class_id,
        )
        session.run("MATCH (e:Entity {class_id: $cid}) DETACH DELETE e", cid=class_id)
        session.run("MATCH (c:DocumentClass {id: $cid}) DETACH DELETE c", cid=class_id)

    for doc in db.scalars(select(Document).where(Document.neo4j_class_id == class_id)):
        _delete_document_postgres(doc, db)
    db.commit()

    try:
        delete_class_ontology(class_id)
    except Exception:
        pass  # Fuseki indisponible : la classe reste supprimee cote Neo4j/Postgres, meme principe qu'a l'ingestion (US3.4)
    return RedirectResponse("/creator/classes", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/classes/{class_id}/concepts/delete")
def delete_concept_route(
    class_id: str, label: str = Form(...), user: User = Depends(require_role(Role.creator)),
):
    """Ajoute le 2026-09-28 (gouvernance) : purger un concept induit a tort
    (ex. "Concept", "Symbole" observes reellement comme types d'entites peu
    utiles lors du test de la reconnaissance LaTeX) sans supprimer toute la
    classe."""
    driver = get_driver()
    with driver.session() as session:
        row = session.run(
            "MATCH (c:DocumentClass {id: $cid})-[:HAS_CONCEPT]->(concept:Concept {label: $label}) RETURN concept.uri AS uri",
            cid=class_id, label=label,
        ).single()
        session.run(
            "MATCH (c:DocumentClass {id: $cid})-[:HAS_CONCEPT]->(concept:Concept {label: $label}) DETACH DELETE concept",
            cid=class_id, label=label,
        )
    try:
        # URI reelle du concept (celle de sa classe d'origine apres une fusion), voir ontology.delete_concept
        ontology_delete_concept(class_id, label, row["uri"] if row else None)
    except Exception:
        pass
    return RedirectResponse(f"/creator/classes/{class_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/corpus")
def list_corpus(request: Request, user: User = Depends(require_role(Role.creator))):
    """US3.17 : vue d'ensemble des corpus - meme donnees que /creator/classes,
    presentees comme point d'entree "corpus documentaires". US7.6 (etendue) :
    suggestions de fusion entre corpus proches, a valider ou rejeter."""
    driver = get_driver()
    with driver.session() as session:
        rows = list(session.run(
            "MATCH (c:DocumentClass) "
            "WHERE NOT coalesce(c.status, '') STARTS WITH 'fusionnee' "
            "OPTIONAL MATCH (c)<-[:IN_CLASS]-(d:Document) "
            "RETURN c.id AS id, c.name AS name, c.status AS status, "
            "       count(DISTINCT d) AS document_count "
            "ORDER BY document_count DESC"
        ))
        suggestions = list(session.run(
            "MATCH (a:DocumentClass)-[r:SIMILAR_TO {status: 'suggested'}]-(b:DocumentClass) "
            "WHERE a.id < b.id "
            "RETURN a.id AS a_id, a.name AS a_name, b.id AS b_id, b.name AS b_name, "
            "       r.score AS score, r.structural_score AS structural_score, r.semantic_score AS semantic_score "
            "ORDER BY r.score DESC"
        ))
    corpus = [dict(row) for row in rows]
    return templates.TemplateResponse(
        request, "creator_corpus.html", {"corpus": corpus, "suggestions": suggestions, "user": user},
    )


@router.get("/corpus/{class_id}")
def corpus_detail(
    request: Request, class_id: str, db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.creator)),
):
    """2026-10-02 (bug signale par l'utilisateur : "la page corpus donne une
    liste des corpus mais qui dirige vers les details de la classe reliee,
    ca semble etre un bug") : un corpus est bien le meme objet Neo4j qu'une
    classe (decide le 2026-09-27), mais la vue CORPUS n'avait pas sa propre
    page - chaque ligne renvoyait vers l'ecran de l'ONTOLOGIE de la classe.
    Page dediee : documents du corpus (avec leur ligne Postgres, donc
    ouvrables/telechargeables), taille, taxonomies construites depuis ce
    corpus ; l'ecran classe reste accessible par un lien explicite."""
    driver = get_driver()
    with driver.session() as session:
        head = session.run(
            "MATCH (c:DocumentClass {id: $cid}) "
            "OPTIONAL MATCH (c)-[:HAS_CONCEPT]->(concept:Concept) "
            "RETURN c.name AS name, c.status AS status, count(DISTINCT concept) AS concept_count",
            cid=class_id,
        ).single()
        if head is None or head["name"] is None:
            return templates.TemplateResponse(
                request, "creator_corpus_detail.html",
                {"class_id": class_id, "name": None, "user": user}, status_code=404,
            )
        entity_count = session.run(
            "MATCH (e:Entity {class_id: $cid}) RETURN count(e) AS c", cid=class_id,
        ).single()["c"]
        scores = {
            r["sha256"]: dict(r) for r in session.run(
                "MATCH (c:DocumentClass {id: $cid})<-[r:IN_CLASS]-(d:Document) "
                "RETURN d.sha256 AS sha256, r.score AS score, r.structural_score AS structural_score, "
                "       r.semantic_score AS semantic_score",
                cid=class_id,
            )
        }
        taxonomies = list(session.run(
            "MATCH (t:Taxonomy)-[:FROM_CORPUS]->(c:DocumentClass {id: $cid}) "
            "RETURN t.id AS id, t.name AS name, t.concept_count AS concept_count ORDER BY t.created_at DESC",
            cid=class_id,
        ))
    documents = list(db.scalars(
        select(Document).where(Document.neo4j_class_id == class_id).order_by(Document.created_at.desc())
    ))
    return templates.TemplateResponse(
        request, "creator_corpus_detail.html",
        {
            "class_id": class_id, "name": head["name"], "status": head["status"],
            "concept_count": head["concept_count"], "entity_count": entity_count,
            "documents": documents, "scores": scores, "taxonomies": taxonomies,
            "threshold": settings.recognition_threshold, "user": user,
        },
    )


@router.post("/corpus/reevaluate-unknown")
def reevaluate_unknown_route(user: User = Depends(require_role(Role.creator))):
    """EPIC-IAF-E17 : rejoue fusion par densite et promotion sur toutes les
    classes inconnues (apres un changement de seuil, par exemple)."""
    class_lifecycle.reevaluate_provisional_classes()
    return RedirectResponse("/creator/corpus", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/corpus/batch-cluster")
def batch_cluster_route(user: User = Depends(require_role(Role.creator))):
    """Ajoute le 2026-09-29 : "construire le corpus a partir de n documents
    similaires" - compare toutes les classes actives entre elles en un lot,
    plutot que seulement une classe neuve contre les autres. Les suggestions
    creees se valident normalement sur cette meme page."""
    class_merge.batch_cluster_classes()
    return RedirectResponse("/creator/corpus", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/classes/merge")
def merge_classes_route(
    class_a: str = Form(...),
    class_b: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.creator)),
):
    """US7.6 (etendue) : le creator valide une suggestion de fusion."""
    driver = get_driver()
    with driver.session() as session:
        class_merge.merge_classes(session, db, class_a, class_b)
    return RedirectResponse("/creator/corpus", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/classes/dismiss-suggestion")
def dismiss_suggestion_route(
    class_a: str = Form(...),
    class_b: str = Form(...),
    user: User = Depends(require_role(Role.creator)),
):
    driver = get_driver()
    with driver.session() as session:
        session.run(
            "MATCH (a:DocumentClass {id: $a})-[r:SIMILAR_TO]-(b:DocumentClass {id: $b}) SET r.status = 'dismissed'",
            a=class_a, b=class_b,
        )
    return RedirectResponse("/creator/corpus", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/settings")
def settings_form(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.creator))):
    """US7.6 (etendue) : "dans les parametres on peut autoriser le merge
    automatique et definir 2 seuils"."""
    platform = class_merge.get_platform_settings(db)
    lifecycle = {
        "official_class_min_documents": settings.official_class_min_documents,
        "merge_similarity_floor": settings.merge_similarity_floor,
        "merge_density_k": settings.merge_density_k,
        "merge_density_min_pairs": settings.merge_density_min_pairs,
        "corpus_seed_min_similarity": settings.corpus_seed_min_similarity,
        "ontology_reduction_min_support": settings.ontology_reduction_min_support,
        "recognition_threshold": settings.recognition_threshold,
    }
    return templates.TemplateResponse(
        request, "creator_settings.html", {"platform": platform, "lifecycle": lifecycle, "user": user},
    )


@router.post("/settings")
def settings_submit(
    auto_merge_enabled: bool = Form(False),
    auto_merge_threshold: float = Form(...),
    suggest_merge_threshold: float = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.creator)),
):
    platform = class_merge.get_platform_settings(db)
    platform.auto_merge_enabled = auto_merge_enabled
    platform.auto_merge_threshold = max(0.0, min(1.0, auto_merge_threshold))
    platform.suggest_merge_threshold = max(0.0, min(1.0, suggest_merge_threshold))
    db.commit()
    return RedirectResponse("/creator/settings", status_code=status.HTTP_303_SEE_OTHER)


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


@router.get("/taxonomies")
def list_taxonomies(request: Request, user: User = Depends(require_role(Role.creator))):
    """US3.18 : taxonomies SKOS deja construites + formulaire pour en
    construire une nouvelle a partir des corpus choisis."""
    driver = get_driver()
    with driver.session() as session:
        taxonomies = list(session.run(
            "MATCH (t:Taxonomy) "
            "OPTIONAL MATCH (t)-[:FROM_CORPUS]->(c:DocumentClass) "
            "RETURN t.id AS id, t.name AS name, t.created_at AS created_at, t.concept_count AS concept_count, "
            "       collect(c.name) AS corpus_names "
            "ORDER BY t.created_at DESC"
        ))
        corpus_options = list(session.run(
            "MATCH (c:DocumentClass) WHERE NOT coalesce(c.status, '') STARTS WITH 'fusionnee' "
            "RETURN c.id AS id, c.name AS name ORDER BY c.name"
        ))
    return templates.TemplateResponse(
        request, "creator_taxonomies.html",
        {"taxonomies": taxonomies, "corpus_options": corpus_options, "error": None, "user": user},
    )


@router.post("/taxonomies/build")
def build_taxonomy_route(
    request: Request,
    name: str = Form(...),
    class_ids: list[str] = Form(...),
    user: User = Depends(require_role(Role.creator)),
):
    try:
        taxonomy_id = taxonomy_builder.build_taxonomy(name.strip() or "Taxonomie", class_ids)
    except ValueError as exc:
        driver = get_driver()
        with driver.session() as session:
            taxonomies = list(session.run(
                "MATCH (t:Taxonomy) OPTIONAL MATCH (t)-[:FROM_CORPUS]->(c:DocumentClass) "
                "RETURN t.id AS id, t.name AS name, t.created_at AS created_at, t.concept_count AS concept_count, "
                "       collect(c.name) AS corpus_names ORDER BY t.created_at DESC"
            ))
            corpus_options = list(session.run(
                "MATCH (c:DocumentClass) WHERE NOT coalesce(c.status, '') STARTS WITH 'fusionnee' "
                "RETURN c.id AS id, c.name AS name ORDER BY c.name"
            ))
        return templates.TemplateResponse(
            request, "creator_taxonomies.html",
            {"taxonomies": taxonomies, "corpus_options": corpus_options, "error": str(exc), "user": user},
            status_code=422,
        )
    return RedirectResponse(f"/creator/taxonomies/{taxonomy_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/taxonomies/{taxonomy_id}")
def taxonomy_detail(request: Request, taxonomy_id: str, user: User = Depends(require_role(Role.creator))):
    driver = get_driver()
    with driver.session() as session:
        head = session.run(
            "MATCH (t:Taxonomy {id: $id}) "
            "OPTIONAL MATCH (t)-[:FROM_CORPUS]->(c:DocumentClass) "
            "RETURN t.name AS name, t.created_at AS created_at, collect(c.name) AS corpus_names",
            id=taxonomy_id,
        ).single()
        if head is None:
            return templates.TemplateResponse(
                request, "creator_taxonomy_detail.html",
                {"taxonomy_id": taxonomy_id, "name": None, "concepts": [], "corpus_names": [], "user": user},
                status_code=404,
            )
        # 2026-10-02 : arbre a PROFONDEUR QUELCONQUE (relations NARROWER
        # recursives) - plus seulement concepts de tete + enfants directs (US3.19).
        top_ids = session.run(
            "MATCH (t:Taxonomy {id: $id})-[:HAS_TAXONOMY_CONCEPT]->(top:TaxonomyConcept) RETURN top.id AS id",
            id=taxonomy_id,
        ).value()
        rows = list(session.run(
            "MATCH (t:Taxonomy {id: $id})-[:HAS_TAXONOMY_CONCEPT]->(:TaxonomyConcept)-[:NARROWER*0..]->(n:TaxonomyConcept) "
            "OPTIONAL MATCH (p:TaxonomyConcept)-[:NARROWER]->(n) "
            "RETURN DISTINCT n.id AS id, n.pref_label AS pref_label, n.alt_labels AS alt_labels, "
            "       n.definition AS definition, p.id AS parent_id",
            id=taxonomy_id,
        ))
        nodes = {
            r["id"]: {"pref_label": r["pref_label"], "alt_labels": r["alt_labels"] or [],
                      "definition": r["definition"], "children": []}
            for r in rows
        }
        for r in rows:
            if r["parent_id"] in nodes:
                nodes[r["parent_id"]]["children"].append(nodes[r["id"]])

        def _sort(node: dict) -> dict:
            node["children"].sort(key=lambda c: (bool(c["children"]) is False, c["pref_label"]))
            for child in node["children"]:
                _sort(child)
            return node

        concepts = sorted((_sort(nodes[i]) for i in top_ids if i in nodes), key=lambda c: c["pref_label"])
    return templates.TemplateResponse(
        request, "creator_taxonomy_detail.html",
        {
            "taxonomy_id": taxonomy_id, "name": head["name"], "corpus_names": head["corpus_names"],
            "concepts": concepts, "user": user,
        },
    )


@router.get("/taxonomies/{taxonomy_id}/export")
@router.get("/taxonomies/{taxonomy_id}/skos.ttl")
def taxonomy_export(taxonomy_id: str, fmt: str = "ttl"):
    fmt = normalize_export_format(fmt)
    content = export_taxonomy_turtle(taxonomy_id, fmt)
    media_type, extension = EXPORT_FORMATS[fmt]
    return Response(
        content=content, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="taxonomie-{taxonomy_id}.{extension}"'},
    )


@router.post("/taxonomies/{taxonomy_id}/delete")
def delete_taxonomy_route(taxonomy_id: str, user: User = Depends(require_role(Role.creator))):
    """Ajoute le 2026-09-28 (gouvernance) : une taxonomie est une vue DERIVEE
    (US3.18/19) - la supprimer n'affecte aucune classe, document ou concept
    source, contrairement a la suppression d'une classe ci-dessus."""
    driver = get_driver()
    with driver.session() as session:
        session.run(
            "MATCH (t:Taxonomy {id: $id})-[:HAS_TAXONOMY_CONCEPT]->(top:TaxonomyConcept)-[:NARROWER*0..]->(child:TaxonomyConcept) "
            "DETACH DELETE child",
            id=taxonomy_id,
        )
        session.run("MATCH (t:Taxonomy {id: $id}) DETACH DELETE t", id=taxonomy_id)
    try:
        ontology_delete_taxonomy(taxonomy_id)
    except Exception:
        pass
    return RedirectResponse("/creator/taxonomies", status_code=status.HTTP_303_SEE_OTHER)
