"""Tests IAF-E16 US16.1/US16.2/US16.3 : generation deterministe (pas de Neo4j
requis - materiau construit a la main) + round-trip REEL avec les vrais
parseurs de lecture (markdown_struct.py, docx_struct.py), pas un simple
relire-ce-qu-on-vient-d-ecrire non verifie contre le pipeline reel."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import docx_struct, markdown_struct  # noqa: E402
from app.doc_generator import (  # noqa: E402
    ClassOntologyMaterial, generate_document, write_docx, write_markdown,
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
