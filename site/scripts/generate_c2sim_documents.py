"""Genere 2 documents a partir de la classe C2SIM (ontologie agregee SISO
C2SIM Core + SMX + LOX, voir scripts/aggregate_ontology.py et
scripts/create_class.py) - demande explicite (2026-10-01) : "genere deux
nouvelles structure. un document word sur le meme format que le precedent
[...] genere un autre document word qui contient des resumes (introduction,
chapitre (resume), chapitre (detail des actions de differents groupes de
troupes), conclusion). Pour le contenu, comme la fois precedente."

Meme architecture que generate_llm_document.py/generate_wine_stories.py :
Gemini (app.graph.chat/chat_json) redige la prose, ancree sur du materiau
REEL - mais C2SIM n'a AUCUNE relation ni attribut entre individus (ontologie
de VOCABULAIRE/codes controles - EchelonCode, TaskActionCode... - pas une
instance de scenario rempli, verifie reellement : extract_ontology_material
renvoie 0 relation/0 attribut sur les 1610 individus). Le materiau reel
utilise ici est donc : nom + type (owl:Class) + VRAIE definition
(rdfs:comment, jamais extraite par ontology_import.py jusqu'ici - lue ici
directement dans le graphe RDF agrege, pas invente).

Document 1 ("recits", meme format que generate_wine_stories.build_docx_document)
: **Corrige le 2026-10-01** - la premiere version racontait directement 3
TERMES de vocabulaire bruts (ex. "ACQUIR" seul), juge pas assez proche d'une
"entite" racontee comme pour le vin. Demande explicite : "Il faut utiliser
l'ontologie pour determiner les entities (generees a partir des entities
types/concepts de l'ontologie). Le fait qu'il n'y ai pas de relation
complique la donne." Nouvelle approche (`synthesize_unit_entities`) : une
"entite" (une Unite militaire) est desormais CONSTRUITE en combinant PLUSIEURS
valeurs REELLES tirees de differentes classes/listes controlees de
l'ontologie (EchelonCode, CountryCode, HostilityStatusCode,
OperationalStatusCode, TaskActionCode) - chaque VALEUR est reelle (et sa
definition, quand elle existe), mais la COMBINAISON (le "profil" de l'unite)
est une construction explicite, pas un individu deja present tel quel dans
l'ontologie (puisqu'aucune relation reelle n'en lie - voir plus bas). Meme
principe de transparence que le tableau "illustratif" du document 2.

Document 2 ("resume operationnel", NOUVELLE structure) : Introduction /
Chapitre "Resume" / Chapitre "Detail des actions de differents groupes de
troupes" / Conclusion. Les "groupes de troupes" = individus REELS de
EchelonCode (SQUAD/PLT/COY/BN/BDE...) ; leurs "actions" = individus REELS de
TaskActionCode. AUCUNE relation reelle n'associe un echelon precis a une
action precise dans cette ontologie (verifie) - l'appariement
echelon<->action ci-dessous est donc un SCENARIO ILLUSTRATIF explicitement
signale comme tel (meme principe que l'analyse oenologique fictive de
generate_wine_stories.py), construit uniquement a partir de noms/definitions
REELS, jamais invente au-dela.

Usage : python scripts/generate_c2sim_documents.py <class_id> <rdf_agrege> <sortie_recits.docx> <sortie_resume.docx>
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rdflib  # noqa: E402
from rdflib.namespace import RDF, RDFS  # noqa: E402

from app.config import settings  # noqa: E402
from app.doc_generator import GeneratedDocument, GeneratedSection, write_docx  # noqa: E402
from app.graph import chat, chat_json  # noqa: E402
from app.ontology_import import extract_ontology_material  # noqa: E402


def _local(uri) -> str:
    s = str(uri)
    return s.split("#")[-1] if "#" in s else s.rsplit("/", 1)[-1]


def load_material_with_comments(rdf_path: Path):
    graph = rdflib.Graph()
    graph.parse(str(rdf_path), format="xml")
    material = extract_ontology_material(graph, source=str(rdf_path))

    # rdfs:comment REEL par individu - jamais extrait par ontology_import.py
    # (verifie : ImportedOntology.entities n'a que name/type). Index par nom
    # local, construit UNE fois ici (pas une requete par entite).
    comments: dict[str, str] = {}
    for s, c in graph.subject_objects(RDFS.comment):
        name = _local(s)
        if name not in comments:  # garde la 1re (certains individus ont plusieurs commentaires)
            comments[name] = str(c).strip()
    return material, comments


def pick_entities_with_comments(material, comments: dict[str, str], etype: str, n: int, seed_filter=None) -> list[dict]:
    candidates = [e for e in material.entities if e["type"] == etype and comments.get(e["name"])]
    if seed_filter:
        candidates = [e for e in candidates if seed_filter(e["name"])]
    return candidates[:n]


# 2026-10-01 : axes utilises pour SYNTHETISER une "entite" Unite militaire -
# chaque classe est une liste controlee REELLE de l'ontologie C2SIM (verifiee
# presente avec des individus nommes). Choisies pour leur pertinence a decrire
# une unite (echelon, nationalite, statut d'allegeance, disponibilite
# operationnelle, action en cours) plutot que pour leur comptage brut.
UNIT_PROFILE_AXES = [
    ("EchelonCode", "echelon"),
    ("CountryCode", "nationalite"),
    ("HostilityStatusCode", "statut_allegeance"),
    ("OperationalStatusCode", "disponibilite"),
    ("TaskActionCode", "action_en_cours"),
]


def synthesize_unit_entities(material, comments: dict[str, str], n: int, seed: int) -> list[dict]:
    """IAF (2026-10-01) - reponse a "utiliser l'ontologie pour determiner les
    entities (generees a partir des entities types/concepts de l'ontologie).
    Le fait qu'il n'y ait pas de relation complique la donne" : puisque
    C2SIM ne contient AUCUNE relation entre individus (verifie, voir
    docstring du module), une "entite" Unite n'existe nulle part toute faite
    dans l'ontologie - elle est ici CONSTRUITE en tirant, pour chaque axe de
    UNIT_PROFILE_AXES, UNE valeur REELLE au hasard (graine fixe, deterministe)
    parmi les individus reels de la classe correspondante. Chaque valeur
    individuelle est 100% reelle (et sa definition quand elle existe) ; seule
    la COMBINAISON est une construction explicite - jamais presentee comme un
    individu deja observe tel quel dans l'ontologie."""
    rng = random.Random(seed)
    entities = []
    for i in range(n):
        profile = {}
        for etype, axis_key in UNIT_PROFILE_AXES:
            pool = [e["name"] for e in material.entities if e["type"] == etype]
            if not pool:
                continue
            value = rng.choice(pool)
            profile[axis_key] = {"value": value, "type": etype, "definition": comments.get(value)}
        entities.append(profile)
    return entities


RECIT_SYSTEM_FR = (
    "Tu rediges un texte en francais, style fiche de renseignement/presentation d'unite C2SIM (standard SISO), "
    "decrivant une unite militaire dont le profil (echelon, nationalite, statut, disponibilite, action en cours) "
    "est donne. Utilise TOUS les elements reels fournis (ne les deforme pas), et tu peux t'appuyer sur tes "
    "connaissances generales du domaine C2/operations militaires pour contextualiser. Precise explicitement que "
    "ce profil d'unite est une COMBINAISON ILLUSTRATIVE de valeurs reelles de l'ontologie, pas une unite "
    "reellement observee. 150-190 mots, un seul paragraphe, pas de titre."
)


def build_recits_document(material, comments: dict[str, str], language: str, seed: int = 20261001) -> GeneratedDocument:
    print("document 1 (.docx) - recits C2SIM (entites synthetisees depuis l'ontologie) :")
    units = synthesize_unit_entities(material, comments, n=3, seed=seed)
    sections: list[GeneratedSection] = []
    for i, profile in enumerate(units, start=1):
        facts = "; ".join(
            f"{axis} = {v['value']}" + (f" ({v['definition']})" if v["definition"] else "")
            for axis, v in profile.items()
        )
        title = f"Unite {i} - echelon {profile.get('echelon', {}).get('value', '?')}"
        story = chat(
            f"Profil REEL (combinaison illustrative) de cette unite : {facts}.",
            model=settings.answer_model, system=RECIT_SYSTEM_FR, max_tokens=500,
        ).strip()
        table = {
            "header": ["Axe (classe ontologique)", "Valeur reelle", "Definition reelle"],
            "rows": [
                [v["type"], v["value"], (v["definition"] or "-")[:150]]
                for v in profile.values()
            ],
        }
        sections.append(GeneratedSection(title=title, paragraphs=[story], table=table))
        print(f"  {title} : {len(story.split())} mots")

    word_count = sum(len(p.split()) for s in sections for p in s.paragraphs)
    return GeneratedDocument(
        title="Recits C2SIM - unites synthetisees depuis l'ontologie", language=language, sections=sections,
        word_count=word_count, requested_words=word_count,
        ground_truth={"mode": "llm-assisted-synthesized-entities", "units": units}, warnings=[
            "chaque unite est une COMBINAISON ILLUSTRATIVE de valeurs reelles (aucune relation reelle "
            "entre individus dans cette ontologie) - voir docstring du module",
        ],
    )


SUMMARY_SYSTEM_FR = (
    "Tu rediges un rapport operationnel en francais, style C2SIM/OTAN, synthetique et factuel. Utilise "
    "UNIQUEMENT les termes et definitions reels fournis, sans en inventer d'autres. Reste neutre et "
    "descriptif (pas de recit dramatise)."
)


def build_operational_summary_document(material, comments: dict[str, str], language: str) -> GeneratedDocument:
    print("\ndocument 2 (.docx) - resume operationnel C2SIM :")
    # Groupes de troupes reels = EchelonCode (SQUAD/PLT/COY/BN/BDE... - verifie
    # reellement present dans l'ontologie C2SIM/LOX). Prefere ceux AVEC
    # definition reelle ; a defaut, le nom seul (certains echelons n'ont pas
    # de commentaire dans le fichier source).
    echelon_names = ["SQUAD", "PLT", "COY", "BN", "BDE"]
    echelons = [e for e in material.entities if e["type"] == "EchelonCode" and e["name"] in echelon_names]
    actions = pick_entities_with_comments(material, comments, "TaskActionCode", len(echelons))
    if len(actions) < len(echelons):
        more = pick_entities_with_comments(
            material, comments, "TaskActionCode", len(echelons) - len(actions),
            seed_filter=lambda n: n not in {a["name"] for a in actions},
        )
        actions = actions + more

    echelon_facts = "; ".join(
        f"{e['name']}" + (f" ({comments[e['name']]})" if comments.get(e["name"]) else "") for e in echelons
    )
    action_facts = "; ".join(f"{a['name']} : {comments[a['name']]}" for a in actions)

    intro = chat(
        f"Ecris l'introduction (100-130 mots, un paragraphe) d'un rapport operationnel presentant la "
        f"situation de plusieurs echelons de troupes reels ({echelon_facts}) menant des actions tactiques "
        f"reelles (vocabulaire C2SIM : {action_facts}).",
        model=settings.answer_model, system=SUMMARY_SYSTEM_FR, max_tokens=400,
    ).strip()
    print(f"  Introduction : {len(intro.split())} mots")

    resume = chat(
        f"Ecris un resume operationnel (120-160 mots, un paragraphe) synthetisant l'engagement de ces "
        f"echelons reels ({echelon_facts}) et de ces actions tactiques reelles ({action_facts}), sans entrer "
        "dans le detail unite par unite (cela viendra dans un chapitre suivant).",
        model=settings.answer_model, system=SUMMARY_SYSTEM_FR, max_tokens=450,
    ).strip()
    print(f"  Chapitre Resume : {len(resume.split())} mots")

    # Chapitre "detail" : UN sous-chapitre par groupe de troupes (echelon
    # reel), chacun associe a UNE action reelle - appariement ILLUSTRATIF
    # explicite (aucune relation reelle echelon<->action dans l'ontologie,
    # voir docstring du module), jamais presente comme un fait extrait.
    pairs = list(zip(echelons, actions))
    detail_prompt = (
        "Pour CHACUN de ces couples (groupe de troupes reel, action tactique reelle C2SIM), redige une courte "
        "description (2-3 phrases) d'un scenario ILLUSTRATIF ou ce groupe mene cette action - precise que "
        "c'est un exemple illustratif, pas un fait reel observe. Couples : "
        + "; ".join(f"{e['name']} (echelon) + {a['name']} ({comments[a['name']]})" for e, a in pairs)
        + '. Reponds en JSON strict : {"groupes": [{"echelon": "...", "action": "...", "description": "..."}, ...]}'
    )
    detail = chat_json(detail_prompt, model=settings.answer_model, system="Tu reponds uniquement en JSON valide.", max_tokens=900)
    detail_rows = [[g["echelon"], g["action"], g["description"]] for g in detail.get("groupes", [])]
    print(f"  Chapitre Detail : {len(detail_rows)} groupe(s)")

    conclusion = chat(
        f"Ecris la conclusion (90-120 mots, un paragraphe) de ce rapport operationnel illustratif portant sur "
        f"les echelons {echelon_facts} et les actions {action_facts}.",
        model=settings.answer_model, system=SUMMARY_SYSTEM_FR, max_tokens=350,
    ).strip()
    print(f"  Conclusion : {len(conclusion.split())} mots")

    sections = [
        GeneratedSection(title="Introduction", paragraphs=[intro]),
        GeneratedSection(title="Chapitre - Resume", paragraphs=[resume]),
        GeneratedSection(
            title="Chapitre - Detail des actions de differents groupes de troupes",
            paragraphs=["Scenario ILLUSTRATIF (appariement groupe/action non extrait de l'ontologie, voir ci-dessous) :"],
            table={"header": ["Groupe de troupes (echelon reel)", "Action (TaskActionCode reel)", "Description illustrative"], "rows": detail_rows},
        ),
        GeneratedSection(title="Conclusion", paragraphs=[conclusion]),
    ]
    word_count = sum(len(p.split()) for s in sections for p in s.paragraphs)
    return GeneratedDocument(
        title="Resume operationnel C2SIM", language=language, sections=sections, word_count=word_count,
        requested_words=word_count,
        ground_truth={
            "mode": "llm-assisted-illustrative-scenario",
            "echelons": [e["name"] for e in echelons], "actions": [a["name"] for a in actions],
        },
        warnings=["appariement groupe de troupes / action explicitement illustratif, pas extrait de l'ontologie"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("rdf_path")
    parser.add_argument("recits_path")
    parser.add_argument("summary_path")
    parser.add_argument("--language", default="fr")
    args = parser.parse_args()

    material, comments = load_material_with_comments(Path(args.rdf_path))
    print(f"materiau charge : {len(material.concepts)} concepts, {len(material.entities)} entites, "
          f"{len(comments)} definition(s) reelle(s)\n")

    doc1 = build_recits_document(material, comments, args.language)
    write_docx(doc1, Path(args.recits_path))

    doc2 = build_operational_summary_document(material, comments, args.language)
    write_docx(doc2, Path(args.summary_path))

    print(f"\necrit : {args.recits_path} ({doc1.word_count} mots)")
    print(f"ecrit : {args.summary_path} ({doc2.word_count} mots)")


if __name__ == "__main__":
    main()
