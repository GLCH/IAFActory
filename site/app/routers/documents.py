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

from .. import class_merge, taxonomy_builder, worker
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
    export_class_owl,
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
                    "relations": [], "concepts": [], "profile": None, "threshold": settings.recognition_threshold,
                    "user": user,
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
    regenere depuis Neo4j. Fusionne desormais le graphe de la classe
    (concepts) et le graphe global des proprietes (relations/attributs,
    US7.9) - bug reel corrige le 2026-09-29, voir ontology.export_class_owl."""
    if not class_graph_has_content(class_id):
        return Response("aucune ontologie pour cette classe", status_code=404)
    content = export_class_owl(class_id)
    return Response(
        content=content, media_type="application/rdf+xml",
        headers={"Content-Disposition": f'attachment; filename="classe-{class_id}.owl"'},
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
        session.run(
            "MATCH (c:DocumentClass {id: $cid})-[:HAS_CONCEPT]->(concept:Concept {label: $label}) DETACH DELETE concept",
            cid=class_id, label=label,
        )
    try:
        ontology_delete_concept(class_id, label)
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
    return templates.TemplateResponse(request, "creator_settings.html", {"platform": platform, "user": user})


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
        # US3.19 : arbre a 2 niveaux (concepts de tete + leurs enfants
        # NARROWER, vide pour un concept isole) - plus la liste plate
        # HAS_TAXONOMY_CONCEPT seule (US3.18).
        top_rows = list(session.run(
            "MATCH (t:Taxonomy {id: $id})-[:HAS_TAXONOMY_CONCEPT]->(top:TaxonomyConcept) "
            "OPTIONAL MATCH (top)-[:NARROWER]->(child:TaxonomyConcept) "
            "RETURN top.pref_label AS pref_label, top.alt_labels AS alt_labels, "
            "       collect(CASE WHEN child IS NULL THEN NULL ELSE "
            "         {pref_label: child.pref_label, alt_labels: child.alt_labels} END) AS children "
            "ORDER BY top.pref_label",
            id=taxonomy_id,
        ))
        concepts = [
            {"pref_label": r["pref_label"], "alt_labels": r["alt_labels"],
             "children": sorted((c for c in r["children"] if c is not None), key=lambda c: c["pref_label"])}
            for r in top_rows
        ]
    return templates.TemplateResponse(
        request, "creator_taxonomy_detail.html",
        {
            "taxonomy_id": taxonomy_id, "name": head["name"], "corpus_names": head["corpus_names"],
            "concepts": concepts, "user": user,
        },
    )


@router.get("/taxonomies/{taxonomy_id}/skos.ttl")
def taxonomy_export(taxonomy_id: str):
    content = export_taxonomy_turtle(taxonomy_id)
    return Response(
        content=content, media_type="text/turtle",
        headers={"Content-Disposition": f'attachment; filename="taxonomie-{taxonomy_id}.ttl"'},
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
