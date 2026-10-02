"""Renseigne les DEFINITIONS manquantes des concepts d'une classe a partir de
WordNet 3.0 (Princeton), dictionnaire libre telecharge le 2026-10-02 dans
site/data/dictionaries/wordnet (source : depot officiel nltk_data,
packages/corpora/wordnet.zip, 10,8 Mo). Demande explicite : "cherche des
fichiers de mots par domaines avec definitions. Un dictionnaire gratuit
serait ideal" - choix de l'utilisateur parmi les sources proposees.

Regles (jamais de definition inventee, US3.17) :
- correspondance EXACTE du libelle (CamelCase eclate, "CabernetSauvignon" ->
  lemme "cabernet_sauvignon") avec un lemme NOM de WordNet - pas de
  correspondance approchee ;
- un lemme a plusieurs sens n'est retenu que si UN sens a une glose du DOMAINE
  (mots-cles --domain, vin par defaut) : "Region" n'est pas renseigne avec la
  definition generique d'une region, ce serait trompeur pour le vin ;
- une definition deja presente (import RDF, saisie du creator) n'est JAMAIS
  ecrasee ;
- la source est conservee (`Concept.definition_source`, `dcterms:source` dans
  Fuseki) - la licence WordNet exige que sa mention de copyright soit
  conservee.

Usage : python scripts/fill_definitions_wordnet.py <class_id> [--dry-run] [--domain wine,grape,...]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ontology  # noqa: E402
from app.graph import get_driver  # noqa: E402

WORDNET_DIR = Path(__file__).resolve().parent.parent / "data" / "dictionaries" / "wordnet"
SOURCE = "WordNet 3.0, Princeton University (https://wordnet.princeton.edu/license-and-commercial-use)"
DEFAULT_DOMAIN = ("wine", "grape", "vine", "vineyard", "winery", "vintage", "claret", "champagne")


def load_noun_glosses() -> dict[str, list[str]]:
    """lemme (minuscules, `_` pour les mots composes) -> definitions de ses
    sens nominaux, DANS L'ORDRE DE FREQUENCE de WordNet (index.noun : les
    offsets d'un lemme sont ranges du sens le plus courant au moins courant ;
    data.noun seul les donnerait dans l'ordre des offsets, bug constate en
    dry-run : "Wine" recevait le sens "une couleur rouge sombre"). Format
    data.noun : `offset lex ss_type w_cnt mot lex_id ... | glose` ; index.noun :
    `lemme pos synset_cnt p_cnt symboles... sense_cnt tagsense_cnt offsets...`."""
    gloss_by_offset: dict[str, str] = {}
    for line in (WORDNET_DIR / "data.noun").read_text(encoding="utf-8").splitlines():
        if line.startswith("  ") or " | " not in line:
            continue
        left, gloss = line.split(" | ", 1)
        definition = re.split(r';\s*"', gloss, maxsplit=1)[0].strip().rstrip(";")  # sans les exemples
        gloss_by_offset[left.split()[0]] = definition

    glosses: dict[str, list[str]] = {}
    for line in (WORDNET_DIR / "index.noun").read_text(encoding="utf-8").splitlines():
        if line.startswith("  "):
            continue
        fields = line.split()
        lemma, synset_count = fields[0], int(fields[2])
        offsets = fields[-synset_count:]
        glosses[lemma.lower()] = [gloss_by_offset[o] for o in offsets if o in gloss_by_offset]
    return glosses


def candidate_lemmas(label: str) -> list[str]:
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", label).lower().split()
    return list(dict.fromkeys(["_".join(words), "".join(words)]))


def pick_definition(senses: list[str], domain: tuple[str, ...]) -> str | None:
    for sense in senses:
        if any(re.search(rf"\b{re.escape(keyword)}", sense.lower()) for keyword in domain):
            return sense
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("class_id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--domain", default=",".join(DEFAULT_DOMAIN))
    args = parser.parse_args()
    domain = tuple(k.strip().lower() for k in args.domain.split(",") if k.strip())

    glosses = load_noun_glosses()
    print(f"WordNet : {len(glosses)} lemmes nominaux charges")

    with get_driver().session() as session:
        concepts = list(session.run(
            "MATCH (:DocumentClass {id: $cid})-[:HAS_CONCEPT]->(c:Concept) "
            "RETURN c.label AS label, coalesce(c.definition, '') AS definition ORDER BY c.label", cid=args.class_id,
        ))
    already = [c["label"] for c in concepts if c["definition"]]
    todo = [c["label"] for c in concepts if not c["definition"]]

    found: dict[str, str] = {}
    ambiguous: list[str] = []
    unknown: list[str] = []
    for label in todo:
        senses: list[str] = []
        for lemma in candidate_lemmas(label):
            senses.extend(glosses.get(lemma, []))
        if not senses:
            unknown.append(label)
            continue
        definition = pick_definition(senses, domain)
        if definition:
            found[label] = definition
        else:
            ambiguous.append(label)

    print(f"{len(concepts)} concept(s) : {len(already)} deja definis (conserves), {len(todo)} a renseigner")
    print(f"  trouves dans WordNet avec un sens du domaine : {len(found)}")
    print(f"  presents mais SANS sens du domaine (non renseignes, definition generique trompeuse) : {len(ambiguous)}")
    print(f"  absents de WordNet : {len(unknown)}")
    for label, definition in sorted(found.items()):
        print(f"    + {label} : {definition[:110]}")
    if args.dry_run:
        print("(dry-run : rien ecrit)")
        return

    with get_driver().session() as session:
        for label, definition in found.items():
            session.run(
                "MATCH (:DocumentClass {id: $cid})-[:HAS_CONCEPT]->(c:Concept {label: $label}) "
                "SET c.definition = $definition, c.definition_source = $source",
                cid=args.class_id, label=label, definition=definition, source=SOURCE,
            )
            ontology.set_concept_definition(args.class_id, label, definition, language="en", source=SOURCE)
    print(f"{len(found)} definition(s) ecrite(s) (Neo4j + Fuseki), source conservee")


if __name__ == "__main__":
    main()
