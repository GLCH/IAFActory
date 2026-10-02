"""Tests IAF-E16 US16.1/US16.2/US16.3 : generation deterministe (pas de Neo4j
requis - materiau construit a la main) + round-trip REEL avec les vrais
parseurs de lecture (markdown_struct.py, docx_struct.py), pas un simple
relire-ce-qu-on-vient-d-ecrire non verifie contre le pipeline reel."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import docx_struct, markdown_struct, pdf_struct  # noqa: E402
from app.doc_generator import (  # noqa: E402
    ClassOntologyMaterial, generate_document, write_docx, write_markdown, write_pdf,
)


def _material(**overrides) -> ClassOntologyMaterial:
    defaults = dict(
        class_id="class-1",
        class_name="Classe de test",
        language="fr",
        concepts=["theorie", "algorithme", "variable"],
        entities=[
            {"name": "Alpha", "type": "Theorie", "attrs": {"portee": "large"}},
            {"name": "Beta", "type": "Algorithme", "attrs": {"complexite": "O(n)"}},
            {"name": "Gamma", "type": "Variable", "attrs": {}},
        ],
        relations=[("Alpha", "Theorie", "utilise", "Beta", "Algorithme")],
        paragraphs_per_section=2.0,
        tables_per_section=1.0,
        real_document_count=3,
        warnings=[],
    )
    defaults.update(overrides)
    return ClassOntologyMaterial(**defaults)


def test_generate_document_is_deterministic_for_a_given_seed():
    material = _material()
    doc_a = generate_document(material, words=100, vocabulary_size=50, seed=7)
    doc_b = generate_document(material, words=100, vocabulary_size=50, seed=7)
    assert doc_a.word_count == doc_b.word_count
    assert doc_a.ground_truth == doc_b.ground_truth


def test_generate_document_rejects_vocabulary_size_outside_fixed_set():
    material = _material()
    with pytest.raises(ValueError):
        generate_document(material, words=100, vocabulary_size=77, seed=1)


def test_generate_document_caps_vocabulary_honestly_never_invents():
    # 3 entites reelles seulement, 50 demandees : reduit honnetement, ne
    # fabrique jamais les 47 manquantes.
    material = _material()
    doc = generate_document(material, words=200, vocabulary_size=50, seed=1)
    assert doc.ground_truth["vocabulary_available"] == 3
    assert doc.ground_truth["vocabulary_used"] == 3
    assert any("reduite" in w for w in doc.warnings)


def test_generate_document_never_uses_entity_names_outside_real_material():
    material = _material()
    doc = generate_document(material, words=200, vocabulary_size=50, seed=3)
    real_names = {e["name"] for e in material.entities}
    used_names = {e["name"] for e in doc.ground_truth["entities"]}
    assert used_names <= real_names


def test_generate_document_stays_shorter_than_budget_when_material_runs_out():
    # Materiau tres pauvre (1 entite, aucune relation/attribut/concept) et un
    # budget de mots ambitieux : le document reste COURT plutot que de
    # repeter du contenu pour combler (voir docstring du module).
    material = _material(
        concepts=[], entities=[{"name": "Solo", "type": "Chose", "attrs": {}}], relations=[],
    )
    doc = generate_document(material, words=5000, vocabulary_size=50, seed=1)
    assert doc.word_count < 5000
    assert any("budget" in w for w in doc.warnings)


def test_write_markdown_roundtrips_through_the_real_parser(tmp_path):
    material = _material()
    doc = generate_document(material, words=300, vocabulary_size=50, seed=2)
    path = tmp_path / "exemple.md"
    write_markdown(doc, path)

    metadata, root = markdown_struct.parse(str(path))
    kinds = [child.kind for child in root.children]
    assert "Section" in kinds
    # Au moins une phrase-gabarit reelle (relation/attribut/concept) doit
    # atterrir comme Paragraph au moins une fois quelque part dans l'arbre.
    def _flatten(elem):
        out = [elem]
        for c in elem.children:
            out.extend(_flatten(c))
        return out
    all_elems = _flatten(root)
    assert any(e.kind == "Paragraph" for e in all_elems)


def _material_with_biblio(**overrides) -> ClassOntologyMaterial:
    # US16.6 : materiau avec une entite "journal" (attributs volume/pages) -
    # doit atterrir dans la section References, pas dans le corps des
    # chapitres.
    return _material(
        entities=[
            {"name": "Alpha", "type": "Theorie", "attrs": {"portee": "large"}},
            {"name": "Beta", "type": "Algorithme", "attrs": {"complexite": "O(n)"}},
            {"name": "Gamma", "type": "Variable", "attrs": {}},
            {"name": "Revue X", "type": "journal", "attrs": {"volume": "12", "pages": "1-10"}},
        ],
        **overrides,
    )


def test_book_structure_has_introduction_chapters_and_references(tmp_path):
    material = _material_with_biblio()
    doc = generate_document(material, words=300, vocabulary_size=50, seed=5, structure="book")

    assert doc.sections[0].title == "Introduction"
    assert doc.sections[-1].title == "References"
    # Au moins un chapitre entre l'introduction et les references, avec des
    # sous-chapitres (imbrication reelle, pas une structure plate).
    chapters = doc.sections[1:-1]
    assert chapters
    assert all(c.subsections for c in chapters)


def test_book_structure_rejects_unknown_structure_name():
    material = _material()
    with pytest.raises(ValueError):
        generate_document(material, words=100, vocabulary_size=50, seed=1, structure="wat")


def test_book_references_use_real_bibliographic_entity_not_invented():
    material = _material_with_biblio()
    doc = generate_document(material, words=300, vocabulary_size=50, seed=5, structure="book")
    references = doc.sections[-1]
    assert references.title == "References"
    assert any("Revue X" in line for line in references.paragraphs)
    assert any("volume" in line and "12" in line for line in references.paragraphs)


def test_book_references_fall_back_to_concept_glossary_without_biblio_entities():
    # Aucune entite bibliographique reelle : repli honnete sur un glossaire
    # des concepts reels, jamais une fausse citation inventee.
    material = _material()  # pas d'entite de type journal/publication/...
    doc = generate_document(material, words=300, vocabulary_size=50, seed=5, structure="book")
    references = doc.sections[-1]
    assert references.title == "References"
    assert references.paragraphs  # au moins la ligne de glossaire


def _flatten(elem):
    out = [elem]
    for c in elem.children:
        out.extend(_flatten(c))
    return out


def test_write_markdown_book_structure_roundtrips_with_nested_headings(tmp_path):
    # Note : le titre du document lui-meme (`# ...`) devient une Section de
    # niveau 1 dans l'arbre du VRAI parseur - Introduction/Chapitre/
    # References (niveau 2) en sont donc des ENFANTS, pas des freres au
    # niveau racine (comportement reel de markdown_struct.parse, pas
    # suppose).
    material = _material_with_biblio()
    doc = generate_document(material, words=300, vocabulary_size=50, seed=5, structure="book")
    path = tmp_path / "livre.md"
    write_markdown(doc, path)

    metadata, root = markdown_struct.parse(str(path))
    all_elems = _flatten(root)
    sections_by_label = {e.label: e for e in all_elems if e.kind == "Section"}
    assert "Introduction" in sections_by_label
    assert "References" in sections_by_label
    # Un chapitre doit avoir un enfant Section (le sous-chapitre) - preuve
    # d'imbrication reelle a la lecture par le VRAI parseur.
    chapter = next(
        e for e in all_elems if e.kind == "Section" and e.label not in ("Introduction", "References")
        and any(c.kind == "Section" for c in e.children)
    )
    assert any(child.kind == "Section" for child in chapter.children)


def test_write_docx_book_structure_roundtrips_with_nested_headings(tmp_path):
    material = _material_with_biblio()
    doc = generate_document(material, words=300, vocabulary_size=50, seed=5, structure="book")
    path = tmp_path / "livre.docx"
    write_docx(doc, path)

    metadata, root = docx_struct.parse(str(path))
    all_elems = _flatten(root)
    sections_by_label = {e.label: e for e in all_elems if e.kind == "Section"}
    assert "Introduction" in sections_by_label
    assert "References" in sections_by_label
    chapter = next(
        e for e in all_elems if e.kind == "Section" and e.label not in ("Introduction", "References")
        and any(c.kind == "Section" for c in e.children)
    )
    assert any(child.kind == "Section" for child in chapter.children)


def test_write_docx_roundtrips_through_the_real_parser(tmp_path):
    material = _material()
    doc = generate_document(material, words=300, vocabulary_size=50, seed=2)
    path = tmp_path / "exemple.docx"
    write_docx(doc, path)

    metadata, root = docx_struct.parse(str(path))

    def _flatten(elem):
        out = [elem]
        for c in elem.children:
            out.extend(_flatten(c))
        return out
    all_elems = _flatten(root)
    assert any(e.kind == "Section" for e in all_elems)
    assert any(e.kind == "Paragraph" for e in all_elems)


# US16.7 : structure "use_case" (Contexte/Acteurs/Scenario/Resultat).

def test_use_case_structure_has_context_actors_scenario_result():
    material = _material()  # a des concepts, entites, relations et attributs reels
    doc = generate_document(material, words=300, vocabulary_size=50, seed=4, structure="use_case")
    titles = [s.title for s in doc.sections]
    assert titles == ["Contexte", "Acteurs", "Scenario", "Resultat"]
    actors = doc.sections[1]
    assert actors.table is not None
    assert actors.table["header"] == ["Entite", "Type"]
    real_names = {e["name"] for e in material.entities}
    assert {row[0] for row in actors.table["rows"]} <= real_names


def test_use_case_scenario_steps_are_numbered_and_use_real_relation():
    material = _material()  # relation reelle : Alpha -utilise-> Beta
    doc = generate_document(material, words=300, vocabulary_size=50, seed=4, structure="use_case")
    scenario = next(s for s in doc.sections if s.title == "Scenario")
    assert scenario.paragraphs
    assert scenario.paragraphs[0].startswith("Etape 1 : ")
    assert "Alpha" in scenario.paragraphs[0] and "Beta" in scenario.paragraphs[0]


def test_use_case_skips_scenario_section_without_real_relations():
    material = _material(relations=[])
    doc = generate_document(material, words=300, vocabulary_size=50, seed=4, structure="use_case")
    titles = [s.title for s in doc.sections]
    assert "Scenario" not in titles
    assert any("Scenario" in w for w in doc.warnings)


def test_write_docx_use_case_roundtrips_through_the_real_parser(tmp_path):
    material = _material()
    doc = generate_document(material, words=300, vocabulary_size=50, seed=4, structure="use_case")
    path = tmp_path / "cas_usage.docx"
    write_docx(doc, path)

    metadata, root = docx_struct.parse(str(path))
    all_elems = _flatten(root)
    sections_by_label = {e.label: e for e in all_elems if e.kind == "Section"}
    assert {"Contexte", "Acteurs", "Scenario", "Resultat"} <= set(sections_by_label)
    assert any(e.kind == "Table" for e in all_elems)


# US16.8 : structure "exposition" (tableaux Concepts/Entites/Relations/Attributs) + writer PDF.

def test_exposition_structure_has_one_table_per_section_with_real_material():
    material = _material()
    doc = generate_document(material, words=300, vocabulary_size=50, seed=6, structure="exposition")
    titles = [s.title for s in doc.sections]
    assert titles == ["Concepts", "Entites", "Relations", "Attributs"]
    for section in doc.sections:
        assert section.table is not None
        assert section.table["rows"]
    concepts_section = doc.sections[0]
    assert concepts_section.table["header"] == ["Concept"]
    relations_section = doc.sections[2]
    assert relations_section.table["header"] == ["Source", "Relation", "Cible"]
    assert relations_section.table["rows"] == [["Alpha", "utilise", "Beta"]]


def test_exposition_omits_sections_without_real_material():
    material = _material(concepts=[], relations=[])
    doc = generate_document(material, words=300, vocabulary_size=50, seed=6, structure="exposition")
    titles = [s.title for s in doc.sections]
    assert titles == ["Entites", "Attributs"]


def test_write_pdf_roundtrips_through_the_real_parser(tmp_path):
    # US16.8 : verifie REELLEMENT (pas suppose) que les tableaux avec
    # bordures GRID sont bien reconnus par pdfplumber.extract_tables() a la
    # lecture, comme documente dans l'epic.
    material = _material()
    doc = generate_document(material, words=300, vocabulary_size=50, seed=6, structure="exposition")
    path = tmp_path / "exposition.pdf"
    write_pdf(doc, path)
    assert path.exists() and path.stat().st_size > 0

    metadata, root = pdf_struct.parse(str(path))
    all_elems = _flatten(root)
    assert any(e.kind == "Section" for e in all_elems)  # une page = une Section
    assert any(e.kind == "Paragraph" for e in all_elems)
    tables = [e for e in all_elems if e.kind == "Table"]
    assert tables, "au moins un tableau doit etre detecte par le vrai parseur PDF (pdfplumber)"
    # Le tableau des relations (3 colonnes, 1 ligne reelle) doit etre repere.
    assert any("Alpha" in t.text and "utilise" in t.text and "Beta" in t.text for t in tables)
