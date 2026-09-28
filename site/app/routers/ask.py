"""US3.5 : le viewer (et le creator) posent une question ; la recherche
vectorielle retrouve les chunks les plus proches, puis la reponse bascule par
resultat entre la voie graphe (chunk dont des entites ont ete extraites -
document reconnu ou classe provisoire structuree) et la voie RAG classique
(chunk sans entite associee). Voir docs/epics/EPIC-IAF-E3-graph-rag.md
(confirme le 2026-09-27) : le viewer ne voit pas cette distinction technique,
seulement la reponse et sa provenance."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.templating import Jinja2Templates

from ..config import settings
from ..deps import require_role
from ..graph import chat, embed, get_driver
from ..models import Role, User

router = APIRouter(dependencies=[Depends(require_role(Role.viewer, Role.creator, Role.admin))])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

TOP_K = 5

ANSWER_SYSTEM = "Tu es un agent qui repond de facon precise et sourcee, sans inventer."


@router.get("/ask")
def ask_form(request: Request, user: User = Depends(require_role(Role.viewer, Role.creator, Role.admin))):
    return templates.TemplateResponse(request, "viewer_ask.html", {"question": None, "answer": None, "hits": [], "user": user})


@router.post("/ask")
def ask_submit(
    request: Request,
    question: str = Form(...),
    user: User = Depends(require_role(Role.viewer, Role.creator, Role.admin)),
):
    ctx = {"question": question, "answer": None, "hits": [], "error": None, "user": user}
    question = question.strip()
    if not question:
        ctx["error"] = "question vide"
        return templates.TemplateResponse(request, "viewer_ask.html", ctx, status_code=422)

    vector = embed(question)
    driver = get_driver()
    with driver.session() as session:
        rows = list(session.run(
            "CALL db.index.vector.queryNodes('chunkEmbeddings', $k, $vector) "
            "YIELD node AS chunk, score "
            "MATCH (el:StructElement)-[:HAS_CHUNK]->(chunk) "
            "MATCH (d:Document)-[:HAS_ELEMENT]->(:StructElement)-[:CHILD*0..]->(el) "
            "OPTIONAL MATCH (chunk)-[:MENTIONS]->(e:Entity) "
            "RETURN chunk.text AS text, el.label AS section, d.filename AS doc, score, "
            "       collect(DISTINCT e.name) AS entities",
            k=TOP_K, vector=vector,
        ))

    if not rows:
        ctx["error"] = "aucun document ingere pour l'instant"
        return templates.TemplateResponse(request, "viewer_ask.html", ctx, status_code=200)

    hits = []
    context_blocks = []
    for row in rows:
        entities = [e for e in row["entities"] if e]
        route = "graphe" if entities else "rag"
        hits.append({
            "doc": row["doc"], "section": row["section"], "score": row["score"],
            "route": route, "entities": entities, "text": row["text"],
        })
        ent_note = f" [entites : {', '.join(entities)}]" if entities else ""
        context_blocks.append(f"- ({row['doc']} / section \"{row['section']}\"){ent_note}\n  {row['text']}")
    ctx["hits"] = hits

    prompt = (
        f"Contexte extrait de documents :\n{chr(10).join(context_blocks)}\n\n"
        f"Question : {question}\n\n"
        "Reponds uniquement a partir du contexte ci-dessus. Cite le document et la section source "
        "entre parentheses. Si le contexte ne permet pas de repondre, dis-le explicitement."
    )
    try:
        ctx["answer"] = chat(prompt, model=settings.answer_model, system=ANSWER_SYSTEM, timeout=90)
    except Exception as exc:
        ctx["error"] = f"reponse indisponible ({type(exc).__name__}: {exc})"

    return templates.TemplateResponse(request, "viewer_ask.html", ctx)
