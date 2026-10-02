"""Genere UN document "dissertation" (Introduction/These/Antithese/
Discussion/Conclusion) + petits tableaux/explications interessants, en
utilisant reellement Gemini (passerelle LLM, `app.graph.chat`/`chat_json`,
meme convention que le pipeline d'ingestion reel - `reasoning_effort:
"disable"`, voir graph.py) pour ECRIRE la prose - PAS une recombinaison
mecanique de gabarits fixes comme `doc_generator.py` (IAF-E16, US16.1-16.8).

Demande explicite (2026-09-30), suite a "le contenu n'est pas suffisamment
bon" sur les documents generes jusqu'ici : "utilise gemini dans le script
pour ecrire les elements attendus dans la structure (introduction, these,
antithese, discussion, conclusion) et des phrases ou des tableaux
interessants pas une colonne avec les noms des regions [...] mets des petits
tableaux (par exemple une commande de vin) et des explications sur certains
vins."

Difference de contrat avec doc_generator.py (a lire absolument avant de
reutiliser ce script sur une autre classe) : ce module n'a PAS la garantie
"jamais de contenu invente" - Gemini REDIGE une prose naturelle ancree sur du
materiau reel (noms d'entites/relations/concepts REELS passes dans le
prompt), mais peut broder autour (connaissances generales d'oenologie) et le
tableau "commande" est explicitement une illustration fictive (quantites/
occasions imaginees). Reutilise UNIQUEMENT les conteneurs de donnees et les
writers de doc_generator.py (GeneratedSection/GeneratedDocument, write_docx/
write_markdown - deja testes, round-trip garanti), jamais ses fonctions
d'assemblage mecanique.

Usage : python scripts/generate_llm_document.py <class_id> <fichier_sortie.docx> [--language fr]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.doc_generator import GeneratedDocument, GeneratedSection, write_docx, write_markdown  # noqa: E402
from app.doc_generator import load_class_material  # noqa: E402
from app.graph import chat, chat_json  # noqa: E402
from app.graph import get_driver  # noqa: E402
from app.config import settings  # noqa: E402

SYSTEM_PROMPT_FR = (
    "Tu rediges un extrait d'un document de reflexion en francais, style dissertation, "
    "sur le vin. Ton style est naturel, engageant, jamais mecanique. Tu t'appuies sur les "
    "noms reels fournis (vins, domaines, regions, cepages) sans jamais en inventer de "
    "nouveaux, mais tu peux t'appuyer sur des connaissances generales d'oenologie pour "
    "developper l'argumentation. 120 a 180 mots, un seul paragraphe, pas de titre."
)

SECTION_PROMPTS = {
    "Introduction": (
        "Ecris l'introduction d'une dissertation sur le vin qui pose la question suivante : "
        "qu'est-ce qui determine vraiment la qualite d'un vin ? Mentionne au moins deux de ces "
        "vins ou domaines reels pour ancrer le propos : {examples}."
    ),
    "These": (
        "Ecris la partie THESE d'une dissertation defendant l'idee que le TERROIR (la region, "
        "le lieu) est le facteur determinant de la qualite d'un vin. Appuie-toi sur ces relations "
        "reelles (vin/domaine situe dans une region) : {examples}."
    ),
    "Antithese": (
        "Ecris la partie ANTITHESE d'une dissertation defendant au contraire l'idee que le CEPAGE "
        "et le savoir-faire du vigneron comptent davantage que le terroir. Appuie-toi sur ces "
        "relations reelles (vin elabore a partir d'un cepage) : {examples}."
    ),
    "Discussion": (
        "Ecris une DISCUSSION qui depasse l'opposition terroir/cepage de la these et de "
        "l'antithese precedentes, en montrant que les deux se combinent. Tu peux citer ces vins "
        "reels : {examples}."
    ),
    "Conclusion": (
        "Ecris la CONCLUSION de cette dissertation sur ce qui determine la qualite d'un vin, en "
        "synthetisant sans repeter mot pour mot la these et l'antithese."
    ),
}


def _sample(items: list, n: int) -> list:
    return items[:n]


def build_dissertation(material, language: str = "fr") -> GeneratedDocument:
    located_in = [r for r in material.relations if r[2].lower() == "locatedin"]
    made_from_grape = [r for r in material.relations if r[2].lower() == "madefromgrape"]
    # 4 bugs reels trouves en testant (2026-09-30), tous lies a un identifiant
    # de "vin" trop approximatif :
    # 1. "wine" in type.lower() attrapait aussi "WineGrape"/"Winery" (pas des
    #    vins) - CabernetFrancGrape propose comme "vin".
    # 2. Apres avoir exclu grape/winery, le meme filtre attrapait encore des
    #    descripteurs de gout de l'ontologie Wine elle-meme (WineTaste/
    #    WineBody... : "Dry", "Full", "Light" proposes comme "vins").
    # 3. Etre la SOURCE d'une relation `locatedIn` ne suffit pas non plus :
    #    dans cette ontologie, les REGIONS ont aussi des `locatedIn` entre
    #    elles (ex. AlsaceRegion locatedIn France) - AlsaceRegion/BordeauxRegion
    #    proposees comme "vins" a commander.
    # 4. `madeFromGrape` seul (mesure : 2 relations, 1 source distincte sur
    #    cette classe - la plupart des individus wine.rdf pointent vers une
    #    expression de restriction OWL anonyme plutot qu'un Grape nomme, filtree
    #    a l'import, IAF-E16 US16.6) est trop rare pour etre utilisable.
    # Mesure reelle des predicats par relation sur cette classe (Counter) :
    # locatedIn=65, hasMaker=52, hasFlavor=43, hasBody=41, hasSugar=40,
    # madeFromGrape=2. hasMaker/hasFlavor/hasBody/hasSugar/hasColor/
    # hasVintageYear sont en revanche SANS AMBIGUITE specifiques a un Wine
    # dans cette ontologie (jamais a une Region/un Grape) - leur union donne
    # 53 vins reels distincts, verifie par les noms obtenus (ex.
    # "ChateauMargaux", "BancroftChardonnay").
    _WINE_SPECIFIC_PREDICATES = {
        "hasmaker", "hasflavor", "hasbody", "hassugar", "hascolor", "madefromgrape", "hasvintageyear",
    }
    wine_source_names = {s for s, _st, r, _t, _tt in material.relations if r.lower() in _WINE_SPECIFIC_PREDICATES}
    wines = [e for e in material.entities if e["name"] in wine_source_names]

    located_examples = ", ".join(f"{s} (a {t})" for s, _st, _r, t, _tt in _sample(located_in, 5)) or "aucune disponible"
    grape_examples = ", ".join(f"{s} (cepage {t})" for s, _st, _r, t, _tt in _sample(made_from_grape, 5)) or "aucune disponible"
    wine_names = [e["name"] for e in _sample(wines, 8)]
    intro_examples = ", ".join(wine_names[:4]) or material.class_name

    print("appel Gemini (passerelle LLM) pour chaque section - peut prendre une minute...")

    def _write_section(title: str) -> GeneratedSection:
        template = SECTION_PROMPTS[title]
        prompt = template.format(examples=intro_examples if title == "Introduction" else (
            located_examples if title == "These" else grape_examples if title == "Antithese" else grape_examples
        ))
        text = chat(prompt, model=settings.answer_model, system=SYSTEM_PROMPT_FR, max_tokens=500)
        print(f"  {title} : {len(text.split())} mots")
        return GeneratedSection(title=title, paragraphs=[text.strip()])

    sections: list[GeneratedSection] = [_write_section(t) for t in ("Introduction", "These", "Antithese", "Discussion")]

    # Petit tableau "interessant" plutot qu'une colonne de noms bruts :
    # exemple illustratif d'une commande de vin (quantites/occasions
    # imaginees par Gemini, ancrees sur des noms de vins REELS de la classe).
    if len(wine_names) >= 3:
        order_prompt = (
            "Propose un exemple illustratif (fictif) de commande de vins pour un diner, en "
            f"choisissant exactement 3 vins PARMI CETTE LISTE REELLE (ne pas en inventer d'autres) : "
            f"{', '.join(wine_names)}. Reponds en JSON strict : "
            '{"rows": [{"vin": "...", "quantite": "...", "occasion": "..."}, ...]}'
        )
        order = chat_json(order_prompt, model=settings.answer_model, system="Tu reponds uniquement en JSON valide.", max_tokens=400)
        rows = [[row["vin"], row["quantite"], row["occasion"]] for row in order.get("rows", [])]
        if rows:
            sections.append(GeneratedSection(
                title="Exemple de commande",
                paragraphs=["Illustration fictive d'une commande, pour quelques vins reels de cette classe."],
                table={"header": ["Vin", "Quantite", "Occasion"], "rows": rows},
            ))
            print(f"  Exemple de commande : {len(rows)} ligne(s)")

    # Explications sur quelques vins reels (prose, pas une colonne de noms).
    if wine_names:
        chosen = _sample(wine_names, 3)
        explain_prompt = (
            f"Pour chacun de ces vins reels : {', '.join(chosen)}, ecris une courte explication "
            "(2-3 phrases) de ce qui le caracterise, en t'appuyant sur des connaissances generales "
            "d'oenologie (cepage, region, style) si tu les connais, sans jamais inventer un fait "
            "precis et verifiable que tu ne connais pas. Reponds en JSON strict : "
            '{"vins": [{"nom": "...", "explication": "..."}, ...]}'
        )
        explanations = chat_json(explain_prompt, model=settings.answer_model, system="Tu reponds uniquement en JSON valide.", max_tokens=600)
        paragraphs = [f"{v['nom']} : {v['explication']}" for v in explanations.get("vins", [])]
        if paragraphs:
            sections.append(GeneratedSection(title="Explications sur quelques vins", paragraphs=paragraphs))
            print(f"  Explications sur quelques vins : {len(paragraphs)} vin(s)")

    sections.append(_write_section("Conclusion"))

    title = f"Dissertation - {material.class_name}"
    word_count = sum(len(p.split()) for s in sections for p in s.paragraphs)
    return GeneratedDocument(
        title=title, language=language, sections=sections, word_count=word_count,
        requested_words=word_count, ground_truth={"class_id": material.class_id, "mode": "llm-assisted"}, warnings=[],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("class_id", help="id Neo4j de la classe documentaire (DocumentClass)")
    parser.add_argument("output_path", help="chemin du fichier a ecrire (.docx ou .md)")
    parser.add_argument("--language", default="fr")
    args = parser.parse_args()

    driver = get_driver()
    with driver.session() as session:
        material = load_class_material(session, args.class_id)

    doc = build_dissertation(material, language=args.language)

    path = Path(args.output_path)
    if path.suffix == ".docx":
        write_docx(doc, path)
    elif path.suffix == ".md":
        write_markdown(doc, path)
    else:
        raise SystemExit("extension non supportee : utiliser .docx ou .md")

    print(f"\necrit : {path} ({doc.word_count} mots, {len(doc.sections)} sections)")


if __name__ == "__main__":
    main()
