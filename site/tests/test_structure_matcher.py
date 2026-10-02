"""Tests US7.2-structurel (2026-10-01) : l'algorithme de reconnaissance de
l'ontologie structurelle d'un document (app/structure_matcher.py), contre
Fuseki REEL (les ontologies/structure/*.ttl sont chargees par
scripts/create_class.py - ces tests supposent Docker demarre, meme
convention que test_ontology_import.py qui a besoin de Neo4j/Fuseki reels)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import structure_matcher  # noqa: E402
from app.struct_element import StructElement  # noqa: E402


def _section(label: str, position: int) -> StructElement:
    return StructElement(kind="Section", label=label, level=1, position=position)


def _table(position: int) -> StructElement:
    return StructElement(kind="Table", label="Tableau", level=2, position=position)


def test_word_form_fully_satisfied_scores_1():
    # Dissertation Word conforme : Introduction(1), Chapitre x2, Discussion(1),
    # Conclusion(1) - respecte exactement les axiomes de iaf-structure-word.ttl.
    elements = [
        _section("Introduction", 1),
        _section("Chapitre 1", 2),
        _section("Sous-chapitre 1.1", 3),
        _section("Chapitre 2", 4),
        _section("Discussion", 5),
        _section("Conclusion", 6),
    ]
    matches = structure_matcher.match_structural_ontologies(elements, "docx")
    word = next(m for m in matches if m.ontology_uri == "urn:iaf:ns:structure-word")
    assert word.score == 1.0, word.violated
    assert not word.violated


def test_word_form_missing_chapter_is_reported_violated():
    # Pas de Chapitre reel (viole le min 1) - Introduction/Discussion/Conclusion
    # presentes mais le score ne doit pas etre parfait.
    elements = [_section("Introduction", 1), _section("Discussion", 2), _section("Conclusion", 3)]
    matches = structure_matcher.match_structural_ontologies(elements, "docx")
    word = next(m for m in matches if m.ontology_uri == "urn:iaf:ns:structure-word")
    assert word.score < 1.0
    assert any("Chapitre" in v or "chapitre" in v.lower() for v in word.violated)


def test_docx_format_never_matched_against_latex_ontology():
    # US explicite : "contraindre cette recherche sur le type de document" -
    # un .docx n'est jamais compare a l'ontologie LaTeX.
    elements = [_section("Introduction", 1)]
    matches = structure_matcher.match_structural_ontologies(elements, "docx")
    assert all(m.ontology_uri != "urn:iaf:ns:structure-latex" for m in matches)


def test_pdf_form_scores_tables_and_pages_honestly():
    # pdf_struct.py reel : chaque PAGE devient une Section (jamais de titre
    # distinct) - verifie que le role PdfSection (AnySection) compte TOUTE
    # Section, pas seulement celles au libelle "section".
    elements = [_section("Page 1", 1), _section("Page 2", 2), _table(3)]
    matches = structure_matcher.match_structural_ontologies(elements, "pdf")
    pdf = next(m for m in matches if m.ontology_uri == "urn:iaf:ns:structure-pdf")
    assert pdf.score == 1.0, pdf.violated  # Header 0<=1 (vacuite), PdfSection 2>=1


def test_latex_form_counts_equation_kind_for_formula_role():
    elements = [
        _section("Abstract", 1), _section("Introduction", 2),
        StructElement(kind="Equation", label="eq1", level=2, position=3),
        _section("Conclusion", 4),
    ]
    matches = structure_matcher.match_structural_ontologies(elements, "tex")
    tex = next(m for m in matches if m.ontology_uri == "urn:iaf:ns:structure-latex")
    assert tex.score == 1.0, tex.violated


def test_real_dissertation_docx_roundtrips_with_partial_word_match(tmp_path):
    # Round-trip REEL (pas un fixture a la main) : une dissertation generee
    # par generate_llm_document.py (Introduction/These/Antithese/Discussion/
    # Conclusion, PAS "Chapitre") relue par le vrai docx_struct.parse. Le
    # match doit etre PARTIEL (These/Antithese ne sont pas des Chapitres par
    # mot-cle) - demontre l'integration bout en bout sans sur-promettre.
    from app import docx_struct
    from app.doc_generator import GeneratedDocument, GeneratedSection, write_docx

    doc = GeneratedDocument(
        title="Test", language="fr",
        sections=[
            GeneratedSection(title="Introduction", paragraphs=["Un paragraphe reel."]),
            GeneratedSection(title="These", paragraphs=["Un paragraphe reel."]),
            GeneratedSection(title="Antithese", paragraphs=["Un paragraphe reel."]),
            GeneratedSection(title="Discussion", paragraphs=["Un paragraphe reel."]),
            GeneratedSection(title="Conclusion", paragraphs=["Un paragraphe reel."]),
        ],
        word_count=10, requested_words=10, ground_truth={}, warnings=[],
    )
    path = tmp_path / "dissertation.docx"
    write_docx(doc, path)

    metadata, root = docx_struct.parse(str(path))
    matches = structure_matcher.match_structural_ontologies(root.children, "docx")
    word = next(m for m in matches if m.ontology_uri == "urn:iaf:ns:structure-word")
    assert 0.0 < word.score < 1.0, word.satisfied
    assert any("Chapitre" in v or "chapitre" in v.lower() for v in word.violated)
