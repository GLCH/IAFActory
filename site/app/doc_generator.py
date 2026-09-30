"""Generation de documents d'exemple par RECOMBINAISON du contenu reel deja
present dans l'ontologie structurelle et semantique d'une classe documentaire
existante (IAF-E16, US16.1). Demande explicite du 2026-09-30 : "générer des
exemples de documents à partir d'une ontologie structurelle et sémantique
[...] taille des documents (nombre de mots total, diversité des termes
utilisés (50, 100, 200, 500 termes différents))".

Decision de cadrage (documentee dans docs/epics/EPIC-IAF-E16-generation-
documents-exemple.md, pas de nouvel appel LLM) : ce module ne genere AUCUN
texte invente. Il lit dans Neo4j le materiau REEL deja extrait d'une classe
(concepts induits US7.1/US7.5, noms et types d'entites US3.4, triplets de
relation observes US13.4/US7.9, cles/valeurs d'attribut observees) et le
recompose dans des phrases-gabarits deterministes (FR/EN). Consequence
directe et recherchee : la "verite terrain" (quelles entites/relations/
attributs ont ete places dans le texte) est connue exactement - matiere
premiere pour le banc d'evaluation US7.7, qui manque aujourd'hui d'un jeu de
documents annotes.

Ontologie structurelle utilisee : ontologies/structure/iaf-structure-base.ttl
(seule existante) pour le vocabulaire d'organisation (Section/Paragraph/
Table) ; la FORME (nombre de paragraphes par section, presence de tableaux)
suit le profil structurel MOYEN reellement observe sur les documents deja
rattaches a la classe (Document.profile_section/paragraph/table, IAF-E7
US7.4) - repli documente si la classe est encore vide de documents reels.

Deux styles de structure (`structure=`, US16.6 ajoute le 2026-09-30, demande
explicite : "génère moi une ontologie structurelle simple (introduction,
chapitres, sous chapitre, références)") :
- `"flat"` (par defaut, US16.1-16.4) : une section = un seul niveau, forme
  dictee par le profil structurel MOYEN reellement observe sur la classe.
- `"book"` (US16.6) : Introduction (fixe, premiere) puis Chapitres contenant
  chacun des Sous-chapitres (imbrication a 2 niveaux, meme principe que
  `taxonomy_builder.py`/US3.19), puis References (fixe, derniere). Vocabulaire
  RDF dedie : `ontologies/structure/iaf-structure-livre.ttl` (specialise
  `iafs:Section` de la base, meme principe que documente dans son
  docstring : "les ontologies structurelles des classes documentaires la
  specialisent"). La section References est construite a partir d'entites
  REELLES dont le type ressemble a une reference bibliographique (heuristique
  `_BIBLIOGRAPHIC_TYPE_HINTS`, ex. "journal" avec ses attributs volume/pages
  reellement extraits par US13.4 sur des documents scientifiques reels) ; si
  aucune n'existe pour la classe, repli honnete sur un simple GLOSSAIRE des
  concepts reels non utilises comme titre de chapitre - jamais de fausse
  citation inventee (auteur/annee/revue fictifs), ce serait exactement le
  type de contenu fabrique que ce module s'interdit.

Limites assumees de cette premiere version (US16.1-16.4, voir US16.5 pour la
suite non faite) :
- "Equation" (STRUCT_KINDS) non modelisee ici - seuls .md et .docx sont
  ecrits dans cette version (US16.2/US16.3), et ni markdown_struct.py ni
  docx_struct.py ne produisent ce type en lecture non plus (seul
  latex_struct.py le fait - US16.5, non fait) ;
- prose MECANIQUE (gabarits), pas un texte naturel - suffisant pour tester
  l'ingestion et calibrer des seuils, pas pour un rendu presentable ;
- si le vocabulaire reel disponible (entites/concepts distincts) est plus
  PAUVRE que `vocabulary_size` demande, le generateur reduit honnetement
  plutot que d'inventer (meme principe que le plafond honnete des graphes
  US3.16/US7.11) ; si les faits reels disponibles s'epuisent avant le budget
  de mots demande, le document genere reste plus COURT que demande plutot
  que de repeter du contenu pour combler - les deux ecarts sont rapportes
  dans `GeneratedDocument.warnings` et le manifeste du lot."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_VOCABULARY_SIZES = (50, 100, 200, 500)
DEFAULT_WORDS = 500
DEFAULT_VOCABULARY_SIZE = 100
STRUCTURE_STYLES = ("flat", "book")

# Non calibre (comme le reste du projet, US7.7) : approximation grossiere du
# nombre de mots d'une phrase-gabarit, utilisee pour dimensionner le nombre
# de sections quand la classe n'a pas encore de documents reels pour en
# deduire un profil structurel moyen.
_WORDS_PER_PARAGRAPH_ESTIMATE = 12
_DEFAULT_PARAGRAPHS_PER_SECTION = 4
_DEFAULT_TABLES_PER_SECTION = 0.3
_MIN_SECTIONS = 2
_MAX_SECTIONS = 12

# US16.6 : forme du style "book" - non calibre, comme le reste du projet.
_MIN_CHAPTERS = 2
_MAX_CHAPTERS = 8
_DEFAULT_SUBCHAPTERS_PER_CHAPTER = 2

# US16.6 : heuristique pour reperer les entites REELLES qui ressemblent a une
# reference bibliographique plutot qu'a un fait du corps du texte (ex. un
# type d'entite "journal" avec des attributs volume/pages reellement extraits
# par US13.4 sur un document scientifique reel) - substring insensible a la
# casse sur Entity.type, jamais une liste figee de types exacts (les libelles
# de type sont un vocabulaire libre invente par le LLM, US3.4).
_BIBLIOGRAPHIC_TYPE_HINTS = ("journal", "publication", "book", "conference", "reference", "source", "article")

_TEMPLATES = {
    "fr": {
        "concept_sentence": "Cette section aborde le concept de {topic}.",
        "type_sentence": "{entity} est une instance de type {type}.",
        "relation_sentence": "{source} est en relation « {relation} » avec {target}.",
        "attribute_sentence": "{entity} a pour {key} : {value}.",
        "table_header": ["Entite", "Attribut", "Valeur"],
        "default_section_titles": ["Introduction", "Contexte", "Analyse", "Synthese", "Discussion", "Conclusion"],
        "doc_title": "Document d'exemple - {name}",
        "introduction_title": "Introduction",
        "references_title": "References",
        "chapter_title_fallback": "Chapitre {n}",
        "subchapter_title_fallback": "Sous-partie {n}",
        "reference_entry": "{entity} - {details}.",
        "references_glossary_intro": "Termes references dans ce document : {terms}.",
    },
    "en": {
        "concept_sentence": "This section addresses the concept of {topic}.",
        "type_sentence": "{entity} is an instance of type {type}.",
        "relation_sentence": '{source} has a "{relation}" relation with {target}.',
        "attribute_sentence": "{entity} has {key}: {value}.",
        "table_header": ["Entity", "Attribute", "Value"],
        "default_section_titles": ["Introduction", "Context", "Analysis", "Summary", "Discussion", "Conclusion"],
        "doc_title": "Example document - {name}",
        "introduction_title": "Introduction",
        "references_title": "References",
        "chapter_title_fallback": "Chapter {n}",
        "subchapter_title_fallback": "Subsection {n}",
        "reference_entry": "{entity} - {details}.",
        "references_glossary_intro": "Terms referenced in this document: {terms}.",
    },
}


def _templates(language: str) -> dict:
    return _TEMPLATES.get(language, _TEMPLATES["fr"])


@dataclass
class ClassOntologyMaterial:
    """Materiau reel d'une classe documentaire, lu depuis Neo4j (US16.1)."""

    class_id: str
    class_name: str
    language: str
    concepts: list[str]
    entities: list[dict]  # {"name": str, "type": str, "attrs": {key: value}}
    relations: list[tuple[str, str, str, str, str]]  # (s_name, s_type, rel_type, t_name, t_type)
    paragraphs_per_section: float
    tables_per_section: float
    real_document_count: int
    warnings: list[str] = field(default_factory=list)


def load_class_material(session, class_id: str) -> ClassOntologyMaterial:
    """Lit le materiau reel d'une classe dans Neo4j. `session` = session du
    driver neo4j (voir graph.get_driver()) - jamais Fuseki (decision de
    cadrage : Neo4j est deja la lecture rapide miroir de toutes les
    ecritures Fuseki de cette classe, ADR 0001)."""
    warnings: list[str] = []

    head = session.run("MATCH (c:DocumentClass {id: $cid}) RETURN c.name AS name", cid=class_id).single()
    if head is None:
        raise ValueError(f"classe introuvable : {class_id}")
    class_name = head["name"] or class_id

    concepts = [
        r["label"] for r in session.run(
            "MATCH (c:DocumentClass {id: $cid})-[:HAS_CONCEPT]->(concept:Concept) "
            "RETURN DISTINCT concept.label AS label ORDER BY label",
            cid=class_id,
        ) if r["label"]
    ]

    entities: list[dict] = []
    for r in session.run(
        "MATCH (e:Entity {class_id: $cid}) RETURN e AS e ORDER BY e.name", cid=class_id,
    ):
        props = dict(r["e"].items())
        name = props.pop("name", None)
        etype = props.pop("type", None)
        props.pop("class_id", None)
        if not name:
            continue
        entities.append({"name": name, "type": etype or "Autre", "attrs": props})

    relations = [
        (r["s_name"], r["s_type"], r["rel_type"], r["t_name"], r["t_type"])
        for r in session.run(
            "MATCH (s:Entity {class_id: $cid})-[r:REL]->(t:Entity {class_id: $cid}) "
            "RETURN s.name AS s_name, s.type AS s_type, r.type AS rel_type, t.name AS t_name, t.type AS t_type "
            "ORDER BY s.name, r.type, t.name",
            cid=class_id,
        )
        if r["s_name"] and r["t_name"] and r["rel_type"]
    ]

    profile = session.run(
        "MATCH (d:Document)-[:IN_CLASS]->(c:DocumentClass {id: $cid}) "
        "RETURN avg(d.profile_section) AS section, avg(d.profile_paragraph) AS paragraph, "
        "       avg(d.profile_table) AS table_, count(d) AS n",
        cid=class_id,
    ).single()
    real_document_count = profile["n"] if profile else 0
    if real_document_count and profile["section"]:
        paragraphs_per_section = (profile["paragraph"] or 0) / profile["section"]
        tables_per_section = (profile["table_"] or 0) / profile["section"]
    else:
        warnings.append(
            "classe sans document reel : profil structurel de repli utilise "
            f"({_DEFAULT_PARAGRAPHS_PER_SECTION} paragraphes/section)"
        )
        paragraphs_per_section = _DEFAULT_PARAGRAPHS_PER_SECTION
        tables_per_section = _DEFAULT_TABLES_PER_SECTION

    lang_rows = list(session.run(
        "MATCH (d:Document)-[:IN_CLASS]->(c:DocumentClass {id: $cid}) "
        "WHERE d.language IS NOT NULL "
        "RETURN d.language AS lang, count(*) AS n ORDER BY n DESC LIMIT 1",
        cid=class_id,
    ))
    language = lang_rows[0]["lang"] if lang_rows else "fr"
    if language not in _TEMPLATES:
        warnings.append(f"langue '{language}' sans gabarit dedie, repli sur 'fr'")
        language = "fr"

    if not entities and not concepts:
        warnings.append("classe sans entite ni concept reel : document genere vide de contenu")

    return ClassOntologyMaterial(
        class_id=class_id, class_name=class_name, language=language, concepts=concepts,
        entities=entities, relations=relations, paragraphs_per_section=paragraphs_per_section,
        tables_per_section=tables_per_section, real_document_count=real_document_count, warnings=warnings,
    )


@dataclass
class GeneratedSection:
    title: str
    paragraphs: list[str]
    table: dict | None = None  # {"header": [str,...], "rows": [[str,...],...]}
    # US16.6 : un seul niveau d'imbrication (chapitre -> sous-chapitres),
    # jamais plus profond - meme principe a 2 niveaux que
    # taxonomy_builder.py/US3.19. Vide pour le style "flat".
    subsections: list["GeneratedSection"] = field(default_factory=list)


@dataclass
class GeneratedDocument:
    title: str
    language: str
    sections: list[GeneratedSection]
    word_count: int
    requested_words: int
    ground_truth: dict
    warnings: list[str]


def _sample_material(
    material: ClassOntologyMaterial, vocabulary_size: int, rng: random.Random,
) -> tuple[list[dict], list[str], list[tuple[str, str, str, str, str]], list[str]]:
    """Echantillonnage honnete partage par les deux styles de structure
    (US16.1/US16.6) - ne fabrique jamais au-dela du materiau reel
    disponible (voir docstring du module)."""
    warnings = list(material.warnings)

    entities = list(material.entities)
    if len(entities) > vocabulary_size:
        entities = rng.sample(entities, vocabulary_size)
        entities.sort(key=lambda e: e["name"])
    elif material.entities and len(entities) < vocabulary_size:
        warnings.append(
            f"diversite demandee ({vocabulary_size} termes) reduite a {len(entities)} : "
            "la classe ne compte pas plus d'entites reelles distinctes"
        )

    concepts = list(material.concepts)
    if len(concepts) > vocabulary_size:
        concepts = rng.sample(concepts, vocabulary_size)
        concepts.sort()

    sampled_names = {e["name"] for e in entities}
    relations = [rel for rel in material.relations if rel[0] in sampled_names and rel[3] in sampled_names]

    if not entities and not concepts:
        warnings.append("aucun fait reel disponible (entites/relations/attributs/concepts) : document vide")

    return entities, concepts, relations, warnings


def _build_fact_sentences(
    entities: list[dict], relations: list[tuple[str, str, str, str, str]], tpl: dict, rng: random.Random,
) -> tuple[list[str], list[tuple[str, str, str]]]:
    """Construit la liste des FAITS reels a recomposer en phrases : relations
    d'abord (le contenu le plus informatif), puis attributs, puis un rappel
    de type pour les entites echantillonnees qu'aucune relation n'a deja
    mentionnees (chaque entite du vocabulaire choisi apparait au moins une
    fois dans le texte)."""
    fact_sentences: list[str] = []
    used_entity_names: set[str] = set()
    for s_name, _s_type, rel_type, t_name, _t_type in relations:
        fact_sentences.append(tpl["relation_sentence"].format(source=s_name, relation=rel_type, target=t_name))
        used_entity_names.add(s_name)
        used_entity_names.add(t_name)

    attribute_facts: list[tuple[str, str, str]] = []
    for e in entities:
        for key, value in e["attrs"].items():
            attribute_facts.append((e["name"], key, str(value)))
    rng.shuffle(attribute_facts)
    for name, key, value in attribute_facts:
        fact_sentences.append(tpl["attribute_sentence"].format(entity=name, key=key, value=value))

    for e in entities:
        if e["name"] not in used_entity_names:
            fact_sentences.append(tpl["type_sentence"].format(entity=e["name"], type=e["type"]))
            used_entity_names.add(e["name"])

    return fact_sentences, attribute_facts


def generate_document(
    material: ClassOntologyMaterial, words: int = DEFAULT_WORDS,
    vocabulary_size: int = DEFAULT_VOCABULARY_SIZE, seed: int | None = None, structure: str = "flat",
) -> GeneratedDocument:
    if vocabulary_size not in ALLOWED_VOCABULARY_SIZES:
        raise ValueError(f"vocabulary_size doit etre l'un de {ALLOWED_VOCABULARY_SIZES}, recu {vocabulary_size}")
    if words <= 0:
        raise ValueError("words doit etre positif")
    if structure not in STRUCTURE_STYLES:
        raise ValueError(f"structure doit etre l'un de {STRUCTURE_STYLES}, recu {structure!r}")

    rng = random.Random(seed)
    tpl = _templates(material.language)
    entities, concepts, relations, warnings = _sample_material(material, vocabulary_size, rng)

    if structure == "book":
        sections, word_count, extra_warnings = _assemble_book(material, entities, concepts, relations, tpl, words, rng)
    else:
        sections, word_count, extra_warnings = _assemble_flat(material, entities, concepts, relations, tpl, words, rng)
    warnings.extend(extra_warnings)

    title = tpl["doc_title"].format(name=material.class_name)
    ground_truth = {
        "class_id": material.class_id,
        "class_name": material.class_name,
        "language": material.language,
        "structure": structure,
        "entities": [{"name": e["name"], "type": e["type"]} for e in entities],
        "relations": [
            {"source": s, "source_type": st, "relation": r, "target": t, "target_type": tt}
            for s, st, r, t, tt in relations
        ],
        "attributes": [
            {"entity": e["name"], "key": k, "value": str(v)} for e in entities for k, v in e["attrs"].items()
        ],
        "concepts": concepts,
        "vocabulary_requested": vocabulary_size,
        "vocabulary_available": len(material.entities),
        "vocabulary_used": len(entities),
    }

    return GeneratedDocument(
        title=title, language=material.language, sections=sections, word_count=word_count,
        requested_words=words, ground_truth=ground_truth, warnings=warnings,
    )


def _assemble_flat(
    material: ClassOntologyMaterial, entities: list[dict], concepts: list[str],
    relations: list[tuple[str, str, str, str, str]], tpl: dict, words: int, rng: random.Random,
) -> tuple[list[GeneratedSection], int, list[str]]:
    warnings: list[str] = []
    fact_sentences, attribute_facts = _build_fact_sentences(entities, relations, tpl, rng)

    # Dimensionnement des sections a partir du profil structurel REEL de la
    # classe (ou du repli documente si la classe n'a pas encore de document).
    target_paragraphs = max(1, round(words / _WORDS_PER_PARAGRAPH_ESTIMATE))
    paragraphs_per_section = max(1.0, material.paragraphs_per_section)
    n_sections = max(_MIN_SECTIONS, min(_MAX_SECTIONS, round(target_paragraphs / paragraphs_per_section)))
    if not concepts and not fact_sentences:
        n_sections = 0

    default_titles = tpl["default_section_titles"]
    sections: list[GeneratedSection] = []
    fact_iter = iter(fact_sentences)
    word_count = 0
    attribute_pool = list(attribute_facts)  # reserve pour les tableaux, distinct du flux de paragraphes

    facts_exhausted = False
    for i in range(n_sections):
        if word_count >= words:
            break
        title = concepts[i % len(concepts)] if concepts else default_titles[i % len(default_titles)]
        paragraphs: list[str] = []
        if concepts:
            opening = tpl["concept_sentence"].format(topic=title)
            paragraphs.append(opening)
            word_count += len(opening.split())

        n_paragraphs_this_section = max(1, round(paragraphs_per_section))
        for _ in range(n_paragraphs_this_section):
            if word_count >= words:
                break
            sentence = next(fact_iter, None)
            if sentence is None:
                facts_exhausted = True
                break
            paragraphs.append(sentence)
            word_count += len(sentence.split())

        table = None
        if attribute_pool and rng.random() < material.tables_per_section:
            rows = [attribute_pool.pop() for _ in range(min(3, len(attribute_pool)))]
            table = {"header": tpl["table_header"], "rows": [[name, key, value] for name, key, value in rows]}

        if paragraphs or table:
            sections.append(GeneratedSection(title=title, paragraphs=paragraphs, table=table))

        if facts_exhausted and not concepts:
            break

    if word_count < words and fact_sentences:
        warnings.append(
            f"budget de {words} mots non atteint ({word_count} mots reels) : "
            "le materiau reel disponible pour cette classe s'est epuise avant, aucun contenu invente pour combler"
        )
    return sections, word_count, warnings


def _assemble_book(
    material: ClassOntologyMaterial, entities: list[dict], concepts: list[str],
    relations: list[tuple[str, str, str, str, str]], tpl: dict, words: int, rng: random.Random,
) -> tuple[list[GeneratedSection], int, list[str]]:
    """US16.6 : Introduction (fixe, premiere) - Chapitres (chacun avec des
    Sous-chapitres, imbrication a 2 niveaux) - References (fixe, derniere,
    jamais inventee - voir docstring du module)."""
    warnings: list[str] = []

    reference_entities = [
        e for e in entities if any(hint in e["type"].lower() for hint in _BIBLIOGRAPHIC_TYPE_HINTS)
    ]
    body_entities = [e for e in entities if e not in reference_entities]
    body_relations = [
        rel for rel in relations
        if rel[0] not in {e["name"] for e in reference_entities} and rel[3] not in {e["name"] for e in reference_entities}
    ]
    fact_sentences, attribute_facts = _build_fact_sentences(body_entities, body_relations, tpl, rng)

    target_paragraphs = max(1, round(words / _WORDS_PER_PARAGRAPH_ESTIMATE))
    paragraphs_per_subchapter = max(1.0, material.paragraphs_per_section)
    n_chapters = max(
        _MIN_CHAPTERS,
        min(_MAX_CHAPTERS, round(target_paragraphs / (paragraphs_per_subchapter * _DEFAULT_SUBCHAPTERS_PER_CHAPTER))),
    )
    if not concepts and not fact_sentences:
        n_chapters = 0

    fact_iter = iter(fact_sentences)
    word_count = 0
    facts_exhausted = False

    # Bug reel trouve en testant (2026-09-30) : cycler `concepts` (liste
    # triee, ordre fixe) avec un curseur qui avance d'exactement
    # `_DEFAULT_SUBCHAPTERS_PER_CHAPTER + 1` titres par chapitre retombe sur
    # le MEME concept a chaque chapitre des que `len(concepts)` divise ce pas
    # (constate avec 3 concepts et 2 sous-chapitres/chapitre - pas de 3 - le
    # meme titre "algorithme" pour tous les chapitres). Un melange (a la
    # graine) casse cet alignement periodique ; `concepts` (trie) reste
    # inchange pour la verite terrain.
    title_concepts = list(concepts)
    rng.shuffle(title_concepts)

    def _next_paragraphs(topic: str | None) -> list[str]:
        nonlocal word_count, facts_exhausted
        paragraphs: list[str] = []
        if topic:
            opening = tpl["concept_sentence"].format(topic=topic)
            paragraphs.append(opening)
            word_count += len(opening.split())
        n_paragraphs = max(1, round(paragraphs_per_subchapter))
        for _ in range(n_paragraphs):
            if word_count >= words:
                break
            sentence = next(fact_iter, None)
            if sentence is None:
                facts_exhausted = True
                break
            paragraphs.append(sentence)
            word_count += len(sentence.split())
        return paragraphs

    sections: list[GeneratedSection] = []

    intro_topic = title_concepts[0] if title_concepts else None
    intro_paragraphs = _next_paragraphs(intro_topic)
    if intro_paragraphs:
        sections.append(GeneratedSection(title=tpl["introduction_title"], paragraphs=intro_paragraphs))

    concept_cursor = 1 if title_concepts else 0
    for c in range(n_chapters):
        if word_count >= words or (facts_exhausted and not title_concepts):
            break
        chapter_title = (
            title_concepts[concept_cursor % len(title_concepts)]
            if title_concepts else tpl["chapter_title_fallback"].format(n=c + 1)
        )
        concept_cursor += 1
        subsections: list[GeneratedSection] = []
        for s in range(_DEFAULT_SUBCHAPTERS_PER_CHAPTER):
            if word_count >= words or (facts_exhausted and not title_concepts):
                break
            sub_topic = title_concepts[concept_cursor % len(title_concepts)] if title_concepts else None
            if title_concepts:
                concept_cursor += 1
            sub_title = sub_topic or tpl["subchapter_title_fallback"].format(n=s + 1)
            paragraphs = _next_paragraphs(sub_topic if sub_topic != chapter_title else None)
            if paragraphs:
                subsections.append(GeneratedSection(title=sub_title, paragraphs=paragraphs))
        if subsections:
            sections.append(GeneratedSection(title=chapter_title, paragraphs=[], subsections=subsections))

    # References : jamais inventees. Priorite aux entites REELLES
    # ressemblant a des references bibliographiques (heuristique de type,
    # voir _BIBLIOGRAPHIC_TYPE_HINTS) ; a defaut, glossaire des concepts
    # reels non utilises comme titre de chapitre/sous-chapitre - jamais de
    # troisieme repli invente.
    if reference_entities:
        reference_lines = []
        for e in reference_entities:
            details = ", ".join(f"{k} : {v}" for k, v in e["attrs"].items()) or e["type"]
            reference_lines.append(tpl["reference_entry"].format(entity=e["name"], details=details))
        sections.append(GeneratedSection(title=tpl["references_title"], paragraphs=reference_lines))
    elif title_concepts:
        remaining_concepts = title_concepts[concept_cursor:] or concepts
        line = tpl["references_glossary_intro"].format(terms=", ".join(remaining_concepts))
        sections.append(GeneratedSection(title=tpl["references_title"], paragraphs=[line]))

    if word_count < words and fact_sentences:
        warnings.append(
            f"budget de {words} mots non atteint ({word_count} mots reels) : "
            "le materiau reel disponible pour cette classe s'est epuise avant, aucun contenu invente pour combler"
        )
    return sections, word_count, warnings


def _write_markdown_section(lines: list[str], section: GeneratedSection, heading_level: int) -> None:
    marker = "#" * min(heading_level, 6)
    lines.append(f"{marker} {section.title}")
    lines.append("")
    for paragraph in section.paragraphs:
        lines.append(paragraph)
        lines.append("")
    if section.table:
        header = section.table["header"]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "|".join("---" for _ in header) + "|")
        for row in section.table["rows"]:
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
    for sub in section.subsections:
        _write_markdown_section(lines, sub, heading_level + 1)


def write_markdown(doc: GeneratedDocument, path: Path) -> None:
    """US16.2/US16.6. Conventions EXACTEMENT celles de markdown_struct.py (pas
    une syntaxe Markdown generique non verifiee) : titres '#'/'##'/'###'
    (_HEADING_RE, niveau = nombre de '#', l'imbrication chapitre/sous-chapitre
    du style "book" est geree par la pile de markdown_struct.parse a la
    lecture - pas besoin de code special cote parseur), tableaux '| a | b |'
    avec une ligne de separation faite uniquement de pipes/tirets juste apres
    l'entete (_TABLE_SEP_RE), paragraphes separes par une ligne vide."""
    lines = [f"# {doc.title}", ""]
    for section in doc.sections:
        _write_markdown_section(lines, section, heading_level=2)
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_docx_section(docx_document, section: GeneratedSection, heading_level: int) -> None:
    docx_document.add_heading(section.title, level=min(heading_level, 9))
    for paragraph in section.paragraphs:
        docx_document.add_paragraph(paragraph)
    if section.table:
        header = section.table["header"]
        rows = section.table["rows"]
        table = docx_document.add_table(rows=1 + len(rows), cols=len(header))
        for col, label in enumerate(header):
            table.cell(0, col).text = label
        for r, row in enumerate(rows, start=1):
            for col, value in enumerate(row):
                table.cell(r, col).text = value
    for sub in section.subsections:
        _write_docx_section(docx_document, sub, heading_level + 1)


def write_docx(doc: GeneratedDocument, path: Path) -> None:
    """US16.3/US16.6. Conventions EXACTEMENT celles de docx_struct.py (pas une
    mise en page python-docx quelconque) : style 'Title' pour le titre du
    document (docx_struct le traite comme niveau 1, meme famille que
    'Heading N'), style 'Heading N' pour chaque section - N=1 pour le style
    "flat" ou pour Introduction/Chapitre/References du style "book", N=2 pour
    un Sous-chapitre (l'imbrication est geree par la pile de
    docx_struct.parse a la lecture, memes niveaux que pour un vrai .docx a
    plusieurs niveaux de titres), paragraphes simples, tableau natif
    python-docx."""
    from docx import Document as DocxDocument  # import local : seul ce writer en depend

    docx_document = DocxDocument()
    docx_document.core_properties.title = doc.title
    docx_document.add_heading(doc.title, level=0)
    for section in doc.sections:
        _write_docx_section(docx_document, section, heading_level=1)
    docx_document.save(str(path))
