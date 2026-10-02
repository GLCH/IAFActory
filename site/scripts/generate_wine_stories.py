"""Genere DEUX documents distincts a partir du materiau REEL de la classe
"Vin" (IAF-92, ontologie Wine importee), rediges par Gemini (passerelle LLM,
meme convention que generate_llm_document.py - `reasoning_effort: "disable"`) :

1. Un `.docx` qui RACONTE 2-3 vins reels et leurs regions, en s'appuyant sur
   des concepts interessants de l'ontologie (cepage/style via le TYPE reel
   de l'individu - ex. "Zinfandel", pas juste "Wine" -, domaine/hasMaker,
   region/locatedIn ET sa chaine de regions parentes, corps/hasBody,
   arome/hasFlavor, sucrosite/hasSugar, millesime/hasVintageYear).
2. Un `.pdf`, BROCHURE sur UN SEUL vin reel specifique : une fiche REELLE
   (les memes concepts ontologiques que ci-dessus) + une analyse
   oenologique FICTIVE (taux d'alcool, acidite, pH... - explicitement
   invente, aucune donnee de ce type n'existe dans l'ontologie Wine) +
   accords mets-vins + notes de degustation.

Demande explicite (2026-10-01), apres une premiere tentative jugee pas assez
riche en concepts reels de l'ontologie : "prend des concepts interessants de
l'ontologie [...] on doit retrouver de nombreux concepts de l'ontologie
semantique vin. retente."

3 bugs reels d'identification des "vrais vins" DEJA trouves et corriges dans
generate_llm_document.py (2026-09-30, meme classe) - reutilises ici tels
quels (WINE_SPECIFIC_PREDICATES), voir son docstring pour le detail des 3
bugs (cepages/descripteurs de gout/regions confondus avec des vins).

Usage :
  python scripts/generate_wine_stories.py <class_id> <sortie_docx> <sortie_pdf> \
      [--wines NomVin1,NomVin2,NomVin3] [--brochure-wine NomVin] [--language fr]
Sans --wines/--brochure-wine : choisit automatiquement les vins reels les
plus richement documentes (le plus de relations reelles), avec des regions
distinctes quand possible.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.doc_generator import GeneratedDocument, GeneratedSection, load_class_material, write_docx, write_pdf  # noqa: E402
from app.graph import chat, chat_json, get_driver  # noqa: E402

# Voir generate_llm_document.py pour la mesure reelle qui a etabli cette
# liste (locatedIn/hasMaker/hasFlavor/hasBody/hasSugar/hasColor/
# madeFromGrape/hasVintageYear specifiques aux vins dans cette ontologie -
# jamais une Region ou un Grape).
_WINE_SPECIFIC_PREDICATES = {
    "hasmaker", "hasflavor", "hasbody", "hassugar", "hascolor", "madefromgrape", "hasvintageyear",
}


def real_wine_names(material) -> list[str]:
    sources = {s for s, _st, r, _t, _tt in material.relations if r.lower() in _WINE_SPECIFIC_PREDICATES}
    return [e["name"] for e in material.entities if e["name"] in sources]


def wine_profile(material, name: str) -> dict:
    """Rassemble TOUT le materiau REEL connu sur un vin (type=cepage/style,
    domaine, region + chaine de regions parentes, corps, arome, sucrosite,
    millesime) - les "concepts interessants de l'ontologie" demandes."""
    entity = next(e for e in material.entities if e["name"] == name)
    rels = {r[2].lower(): r[3] for r in material.relations if r[0] == name}

    region_chain = []
    region = rels.get("locatedin")
    seen = set()
    while region and region not in seen:
        region_chain.append(region)
        seen.add(region)
        region = next(
            (r[3] for r in material.relations if r[0] == region and r[2].lower() == "locatedin"), None,
        )

    return {
        "name": name,
        "grape_or_style": entity["type"],
        "maker": rels.get("hasmaker"),
        "region_chain": region_chain,
        "body": rels.get("hasbody"),
        "flavor": rels.get("hasflavor"),
        "sugar": rels.get("hassugar"),
        "color": rels.get("hascolor"),
        "vintage": rels.get("hasvintageyear"),
    }


def _facts_line(p: dict) -> str:
    parts = [f"cepage/style reel : {p['grape_or_style']}"]
    if p["maker"]:
        parts.append(f"domaine reel : {p['maker']}")
    if p["region_chain"]:
        parts.append(f"region reelle (du plus precis au plus large) : {' > '.join(p['region_chain'])}")
    if p["body"]:
        parts.append(f"corps reel : {p['body']}")
    if p["flavor"]:
        parts.append(f"arome reel : {p['flavor']}")
    if p["sugar"]:
        parts.append(f"sucrosite reelle : {p['sugar']}")
    if p["color"]:
        parts.append(f"couleur reelle : {p['color']}")
    if p["vintage"]:
        parts.append(f"millesime reel : {p['vintage']}")
    return " ; ".join(parts)


DOCX_SYSTEM_FR = (
    "Tu rediges un recit en francais sur un vin et sa region, style magazine oenologique. "
    "Utilise TOUS les faits reels fournis (ne les omets pas, ne les invente pas differemment), "
    "et tu peux t'appuyer sur tes connaissances generales (geographie, oenologie) pour "
    "developper le recit autour de la region et du cepage reels. 150-200 mots, un seul "
    "paragraphe, pas de titre, pas de repetition mecanique des faits en liste."
)


def build_docx_document(material, wine_names: list[str], language: str) -> GeneratedDocument:
    sections: list[GeneratedSection] = []
    print("document 1 (.docx) - recits de vins :")
    for name in wine_names:
        profile = wine_profile(material, name)
        facts = _facts_line(profile)
        prompt = (
            f"Ecris le recit du vin '{name}' et de sa region. Faits reels a utiliser : {facts}."
        )
        story = chat(prompt, model=settings.answer_model, system=DOCX_SYSTEM_FR, max_tokens=500).strip()
        table = {
            "header": ["Vin", "Cepage/style", "Domaine", "Region", "Corps", "Arome", "Sucrosite", "Millesime"],
            "rows": [[
                name, profile["grape_or_style"], profile["maker"] or "-",
                profile["region_chain"][0] if profile["region_chain"] else "-",
                profile["body"] or "-", profile["flavor"] or "-", profile["sugar"] or "-",
                profile["vintage"] or "-",
            ]],
        }
        sections.append(GeneratedSection(title=name, paragraphs=[story], table=table))
        print(f"  {name} : {len(story.split())} mots, fiche reelle jointe")

    title = f"Recits de vins et de leurs regions - {material.class_name}"
    word_count = sum(len(p.split()) for s in sections for p in s.paragraphs)
    return GeneratedDocument(
        title=title, language=language, sections=sections, word_count=word_count, requested_words=word_count,
        ground_truth={"class_id": material.class_id, "mode": "llm-assisted", "wines": wine_names}, warnings=[],
    )


BROCHURE_INTRO_SYSTEM_FR = (
    "Tu rediges le texte d'introduction d'une brochure commerciale en francais pour un vin, "
    "ton engageant et valorisant, en t'appuyant STRICTEMENT sur les faits reels fournis (ne "
    "les invente pas differemment). 100-130 mots, un seul paragraphe, pas de titre."
)
BROCHURE_TASTING_SYSTEM_FR = (
    "Tu rediges des notes de degustation en francais pour un vin, style brochure commerciale. "
    "Coherentes avec les faits reels fournis. 80-120 mots, un seul paragraphe, pas de titre."
)


def build_pdf_document(material, wine_name: str, language: str) -> GeneratedDocument:
    print(f"\ndocument 2 (.pdf) - brochure sur {wine_name} :")
    profile = wine_profile(material, wine_name)
    facts = _facts_line(profile)

    intro = chat(
        f"Faits reels sur le vin '{wine_name}' : {facts}.", model=settings.answer_model,
        system=BROCHURE_INTRO_SYSTEM_FR, max_tokens=400,
    ).strip()
    print(f"  intro : {len(intro.split())} mots")

    tasting = chat(
        f"Faits reels sur le vin '{wine_name}' : {facts}.", model=settings.answer_model,
        system=BROCHURE_TASTING_SYSTEM_FR, max_tokens=350,
    ).strip()
    print(f"  notes de degustation : {len(tasting.split())} mots")

    analysis_prompt = (
        f"A partir de ces faits reels sur le vin '{wine_name}' ({facts}), propose une analyse "
        "oenologique PLAUSIBLE mais EXPLICITEMENT FICTIVE (aucune de ces valeurs numeriques "
        "n'existe dans une base de donnees reelle - invente des valeurs coherentes avec le "
        "profil donne). Reponds en JSON strict : {\"taux_alcool\": \"...\", \"acidite_totale\": "
        "\"...\", \"ph\": \"...\", \"sucre_residuel\": \"...\", \"temperature_service\": \"...\", "
        "\"potentiel_garde\": \"...\"}"
    )
    analysis = chat_json(
        analysis_prompt, model=settings.answer_model, system="Tu reponds uniquement en JSON valide.", max_tokens=400,
    )
    print("  analyse oenologique fictive generee")

    pairing_prompt = (
        f"Propose 3 accords mets-vins PLAUSIBLES pour le vin '{wine_name}' (faits reels : {facts}). "
        'Reponds en JSON strict : {"accords": [{"plat": "...", "note": "..."}, ...]}'
    )
    pairing = chat_json(
        pairing_prompt, model=settings.answer_model, system="Tu reponds uniquement en JSON valide.", max_tokens=350,
    )
    print(f"  accords mets-vins : {len(pairing.get('accords', []))} proposition(s)")

    fiche_table = {
        "header": ["Caracteristique", "Valeur (ontologie reelle)"],
        "rows": [
            ["Cepage / style", profile["grape_or_style"]],
            ["Domaine", profile["maker"] or "-"],
            ["Region", " > ".join(profile["region_chain"]) or "-"],
            ["Corps", profile["body"] or "-"],
            ["Arome", profile["flavor"] or "-"],
            ["Sucrosite", profile["sugar"] or "-"],
            ["Millesime", profile["vintage"] or "-"],
        ],
    }
    analysis_table = {
        "header": ["Parametre", "Valeur (illustration fictive)"],
        "rows": [
            ["Taux d'alcool", analysis.get("taux_alcool", "-")],
            ["Acidite totale", analysis.get("acidite_totale", "-")],
            ["pH", analysis.get("ph", "-")],
            ["Sucre residuel", analysis.get("sucre_residuel", "-")],
            ["Temperature de service", analysis.get("temperature_service", "-")],
            ["Potentiel de garde", analysis.get("potentiel_garde", "-")],
        ],
    }
    pairing_table = {
        "header": ["Plat", "Note"],
        "rows": [[a["plat"], a["note"]] for a in pairing.get("accords", [])],
    }

    sections = [
        GeneratedSection(title=wine_name, paragraphs=[intro]),
        GeneratedSection(
            title="Fiche reelle (ontologie)",
            paragraphs=["Donnees issues directement de l'ontologie semantique de la classe."],
            table=fiche_table,
        ),
        GeneratedSection(
            title="Analyse oenologique (illustration fictive)",
            paragraphs=["Valeurs illustratives, INVENTEES pour cette brochure - aucune mesure reelle."],
            table=analysis_table,
        ),
        GeneratedSection(title="Notes de degustation", paragraphs=[tasting]),
    ]
    if pairing_table["rows"]:
        sections.append(GeneratedSection(
            title="Accords mets-vins", paragraphs=["Suggestions illustratives."], table=pairing_table,
        ))

    title = f"Brochure - {wine_name}"
    word_count = sum(len(p.split()) for s in sections for p in s.paragraphs)
    return GeneratedDocument(
        title=title, language=language, sections=sections, word_count=word_count, requested_words=word_count,
        ground_truth={"class_id": material.class_id, "mode": "llm-assisted-fictional-analysis", "wine": wine_name},
        warnings=["analyse oenologique et accords mets-vins explicitement fictifs (illustration)"],
    )


def pick_default_wines(material, n: int) -> list[str]:
    """Choisit les vins reels les plus richement documentes, avec des
    regions distinctes quand possible (pour que "leurs regions" varient)."""
    names = real_wine_names(material)
    counts = Counter(s for s, _st, _r, _t, _tt in material.relations if s in names)
    ranked = [name for name, _c in counts.most_common()]
    chosen: list[str] = []
    seen_regions: set[str] = set()
    for name in ranked:
        region = next(
            (r[3] for r in material.relations if r[0] == name and r[2].lower() == "locatedin"), None,
        )
        if region and region in seen_regions:
            continue
        chosen.append(name)
        if region:
            seen_regions.add(region)
        if len(chosen) >= n:
            break
    return chosen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("class_id")
    parser.add_argument("docx_path")
    parser.add_argument("pdf_path")
    parser.add_argument("--wines", default=None, help="noms de 2-3 vins reels, separes par des virgules")
    parser.add_argument("--brochure-wine", default=None, help="nom du vin reel pour la brochure")
    parser.add_argument("--language", default="fr")
    args = parser.parse_args()

    driver = get_driver()
    with driver.session() as session:
        material = load_class_material(session, args.class_id)

    wine_names = args.wines.split(",") if args.wines else pick_default_wines(material, 3)
    brochure_wine = args.brochure_wine or (
        pick_default_wines(material, 1)[0] if not args.wines else wine_names[0]
    )
    print(f"vins choisis pour le recit : {wine_names}")
    print(f"vin choisi pour la brochure : {brochure_wine}\n")

    docx_doc = build_docx_document(material, wine_names, args.language)
    write_docx(docx_doc, Path(args.docx_path))

    pdf_doc = build_pdf_document(material, brochure_wine, args.language)
    write_pdf(pdf_doc, Path(args.pdf_path))

    print(f"\necrit : {args.docx_path} ({docx_doc.word_count} mots)")
    print(f"ecrit : {args.pdf_path} ({pdf_doc.word_count} mots)")


if __name__ == "__main__":
    main()
