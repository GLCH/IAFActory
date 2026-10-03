"""Genere des documents Word JURIDIQUES a partir de l'ontologie LegalRuleML
(`ontologies/semantic-examples/legalruleml-aggregated.rdf`, OASIS CSPRD02).

Demande explicite (2026-10-02) : 10 documents legaux, structure simple :
Introduction, 3 chapitres (Explications, Exemple detaille, Clarification),
Conclusion, au format Word.

Contrat (comme generate_llm_document.py) : Gemini REDIGE la prose via la
passerelle LLM ; chaque document est ANCRE sur de vrais concepts de l'ontologie
(nom + definition rdfs:comment lus dans le fichier, jamais ecrits de memoire)
et sur un scenario juridique FICTIF (aucune loi, decision ou entreprise reelle).
Le contenu n'est donc pas identique d'un lancement a l'autre ; `manifest.json`
conserve les concepts d'ancrage de chaque document et le resultat de l'analyse
structurelle reelle (`docx_struct.parse`).

Usage (depuis site/) :
  python scripts/generate_legalruleml_documents.py --out ../corpus/documents/legalruleml
  python scripts/generate_legalruleml_documents.py --out /tmp/x --only 01,02
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rdflib  # noqa: E402
from docx import Document  # noqa: E402
from rdflib import RDF, RDFS  # noqa: E402

from app import docx_struct  # noqa: E402
from app.config import settings  # noqa: E402
from app.graph import chat  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ONTOLOGY = REPO_ROOT / "ontologies" / "semantic-examples" / "legalruleml-aggregated.rdf"

# 10 sujets : un groupe de concepts LegalRuleML reels + un scenario fictif.
THEMES = [
    ("01", "obligation-violation-conformite", "Obligations, violation et conformité dans un contrat de livraison",
     ["Obligation", "Violation", "Compliance", "DeonticSpecification", "Bearer"],
     "un contrat de livraison de marchandises entre deux societes fictives, avec delai de livraison"),
    ("02", "permission-droit-interdiction", "Permissions, droits et interdictions dans un règlement d'accès aux données",
     ["Permission", "Right", "Prohibition", "DeonticSpecification", "AuxiliaryParty"],
     "le reglement interieur fictif d'un organisme qui gere des donnees de sante"),
    ("03", "force-stricte-defaisable", "Règles strictes, défaisables et exceptions dans un régime de remboursement",
     ["StrictStrength", "DefeasibleStrength", "Defeater", "Strength", "RuleStatement"],
     "un regime fictif de remboursement de frais avec des cas d'exception"),
    ("04", "priorite-override", "Priorité entre règles : la loi spéciale l'emporte sur la loi générale",
     ["Override", "OverrideStatement", "RuleStatement", "Strength", "Statement"],
     "deux textes fictifs sur la duree d'un preavis, l'un general, l'autre sectoriel"),
    ("05", "sanction-reparation", "Sanctions et réparations liées à une obligation",
     ["PenaltyStatement", "Reparation", "ReparationStatement", "PrescriptiveStatement", "Obligation"],
     "une clause penale fictive en cas de retard de paiement"),
    ("06", "temporalite-statut-juridique", "Temps et statut juridique d'une norme : entrée en vigueur et abrogation",
     ["TemporalCharacteristic", "LegalStatus", "StatusDevelopment", "Times", "Context"],
     "un decret fictif qui entre en vigueur, est modifie puis abroge"),
    ("07", "autorite-juridiction-source", "Autorités, juridictions et sources du droit",
     ["Authority", "Jurisdiction", "LegalSource", "LegalReference", "Source"],
     "une competence territoriale fictive entre deux regions et leurs autorites"),
    ("08", "acteurs-roles-figures", "Acteurs, agents, rôles et figures dans un contrat de location",
     ["Actor", "Agent", "Figure", "Role", "Bearer"],
     "un bail d'habitation fictif entre un bailleur, un locataire et un mandataire"),
    ("09", "enonces-constitutifs-prescriptifs", "Énoncés constitutifs, prescriptifs et factuels dans un code de conduite",
     ["ConstitutiveStatement", "PrescriptiveStatement", "FactualStatement", "LogicalFormulaStatement", "Statement"],
     "un code de conduite fictif qui definit des termes, prescrit des comportements et constate des faits"),
    ("10", "contexte-alternatives-paraphrase", "Contextes, lectures alternatives et paraphrases d'un même article",
     ["Context", "Association", "Alternative", "Alternatives", "Paraphrase", "LegalRuleMLDocument"],
     "un article fictif de loi lu de deux manieres par deux autorites differentes"),
]

SECTIONS = ["Introduction", "Chapitre 1 : Explications", "Chapitre 2 : Exemple détaillé", "Chapitre 3 : Clarification", "Conclusion"]

SYSTEM = (
    "Tu es juriste et redacteur technique. Tu rediges en francais un document juridique pedagogique, precis et "
    "sobre, sur un scenario ENTIEREMENT FICTIF (aucune loi, decision, personne ou entreprise reelle). "
    "Tu respectes EXACTEMENT cette structure, chaque titre sur sa propre ligne precede de '## ' : "
    "## Introduction / ## Chapitre 1 : Explications / ## Chapitre 2 : Exemple détaillé / "
    "## Chapitre 3 : Clarification / ## Conclusion. Pas de titre de document, pas de liste a puces, pas de "
    "tableau, du texte courant en paragraphes. Environ 700 a 900 mots au total."
)


def load_concepts() -> dict[str, dict]:
    graph = rdflib.Graph()
    graph.parse(str(ONTOLOGY))
    concepts: dict[str, dict] = {}
    for kind in (RDFS.Class, RDF.Property):
        for subject in graph.subjects(RDF.type, kind):
            name = str(subject).split("#")[-1].split("/")[-1]
            comment = graph.value(subject, RDFS.comment)
            if name and comment and name not in concepts:
                concepts[name] = {"definition": " ".join(str(comment).split()), "kind": "classe" if kind == RDFS.Class else "propriete"}
    return concepts


def build_prompt(title: str, names: list[str], scenario: str, concepts: dict[str, dict]) -> str:
    lines = "\n".join(f"- {n} : {concepts[n]['definition']}" for n in names)
    return (
        f"Sujet : {title}.\nScenario fictif : {scenario}.\n\n"
        "Concepts de l'ontologie LegalRuleML a mobiliser (nom en anglais conserve, definition fournie) :\n"
        f"{lines}\n\n"
        "Introduction : presente le sujet et les concepts. Chapitre 1 : explique chaque concept clairement. "
        "Chapitre 2 : un exemple detaille et concret qui applique les concepts au scenario fictif, etape par etape. "
        "Chapitre 3 : clarifie les confusions frequentes et les limites entre concepts proches. "
        "Conclusion : synthese courte."
    )


def _fold(text: str) -> str:
    """Minuscules sans accents : le modele ecrit "détaillé" la ou un titre ASCII est attendu."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn").strip()


def split_sections(text: str) -> dict[str, str] | None:
    """Decoupe la reponse selon les titres '## ' attendus (comparaison sans accents ni casse) ;
    None si un titre manque ou si une section a moins de 40 mots."""
    parts = re.split(r"(?m)^##\s*(.+?)\s*$", text)
    found = {_fold(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    if not all(_fold(section) in found and len(found[_fold(section)].split()) >= 40 for section in SECTIONS):
        return None
    return {section: found[_fold(section)] for section in SECTIONS}


def _add_paragraph(doc, text: str) -> None:
    """Paragraphe ; les `**terme**` du Markdown de Gemini deviennent du gras (les concepts de l'ontologie)."""
    paragraph = doc.add_paragraph()
    for index, chunk in enumerate(re.split(r"\*\*", text)):
        if chunk:
            paragraph.add_run(chunk.replace("*", "")).bold = index % 2 == 1


def write_docx(path: Path, title: str, sections: dict[str, str]) -> None:
    doc = Document()
    doc.add_heading(title, level=0)
    for heading, body in sections.items():
        doc.add_heading(heading, level=1)
        for paragraph in [p.strip() for p in body.splitlines() if p.strip()]:
            _add_paragraph(doc, paragraph)
    doc.save(str(path))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="dossier de sortie (cree si besoin)")
    parser.add_argument("--only", help="numeros separes par des virgules (defaut : les 10)")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    concepts = load_concepts()
    wanted = {n.strip() for n in args.only.split(",")} if args.only else None
    manifest = []
    for number, slug, title, names, scenario in THEMES:
        if wanted and number not in wanted:
            continue
        missing = [n for n in names if n not in concepts]
        if missing:
            raise SystemExit(f"concepts absents de l'ontologie : {missing}")
        sections = None
        for _attempt in range(3):
            text = chat(build_prompt(title, names, scenario, concepts), model=settings.answer_model, system=SYSTEM, max_tokens=3000, timeout=240)
            sections = split_sections(text)
            if sections:
                break
        if not sections:
            print(f"{number} {slug} : structure non respectee apres 3 essais, ignore")
            continue
        path = out_dir / f"legal-{number}-{slug}.docx"
        write_docx(path, title, sections)
        _meta, root = docx_struct.parse(str(path))
        headings = [child.label for child in root.children if child.kind == "Section"] if root else []
        words = sum(len(body.split()) for body in sections.values())
        manifest.append({
            "file": path.name, "title": title, "scenario": scenario, "anchor_concepts": names,
            "definitions": {n: concepts[n]["definition"] for n in names}, "words": words,
            "parsed_top_level_sections": len(headings),
        })
        print(f"{path.name} : {words} mots, {len(headings)} section(s) detectee(s) par le parseur")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
