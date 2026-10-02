"""Tests de la taxonomie profonde (2026-10-02) : hierarchie reelle d'abord,
induction LLM VALIDEE ensuite - sans Neo4j/Fuseki/LLM reels (chat_json
remplace), seule la logique de validation et d'arbre est testee."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import taxonomy_builder as tb  # noqa: E402


def _labels(nodes):
    out = []
    for n in nodes:
        out.append(n["pref_label"])
        out.extend(_labels(n["children"]))
    return out


def test_real_forest_follows_subclass_edges_and_cuts_cycles():
    labels = ["A", "B", "C", "D"]
    forest = tb._real_forest(labels, [("B", "A"), ("C", "B"), ("D", "C"), ("A", "D")], {})
    # le cycle A->D->C->B->A est coupe : un arbre unique, tous les concepts presents une fois
    assert sorted(_labels(forest)) == labels
    assert tb.tree_depth(forest) >= 3


def test_real_forest_keeps_definitions_on_nodes():
    forest = tb._real_forest(["A", "B"], [("B", "A")], {"B": "definition reelle"})
    assert forest[0]["children"][0]["definition"] == "definition reelle"


def test_llm_grouping_is_validated_and_keeps_every_concept(monkeypatch):
    forest = [{"pref_label": x, "alt_labels": [x], "children": []} for x in ["Merlot", "Zinfandel", "Pauillac", "Margaux", "Port"]]
    monkeypatch.setattr(tb, "chat_json", lambda *a, **k: {"categories": [
        {"label": "Cepages", "children": ["Merlot", "Zinfandel", "Inconnu"]},
        {"label": "Geographie", "children": [{"label": "Medoc", "children": ["Pauillac", "Margaux"]}]},
    ]})
    tree = tb._group_items_llm(forest, {})
    assert sorted(l for l in _labels(tree) if l in {"Merlot", "Zinfandel", "Pauillac", "Margaux", "Port"}) == \
        ["Margaux", "Merlot", "Pauillac", "Port", "Zinfandel"]  # "Port" omis par le LLM : jamais perdu
    assert "Inconnu" not in _labels(tree)  # aucun concept feuille invente
    # "Geographie" n'a qu'UN enfant (Medoc) : la categorie est repliee, pas de categorie a un seul enfant
    assert "Geographie" not in _labels(tree)
    assert tb.tree_depth(tree) == 2


def test_llm_grouping_rejected_when_output_too_incomplete(monkeypatch):
    forest = [{"pref_label": f"C{i}", "alt_labels": [f"C{i}"], "children": []} for i in range(20)]
    monkeypatch.setattr(tb, "chat_json", lambda *a, **k: {"categories": [{"label": "X", "children": ["C0", "C1"]}]})
    assert tb._group_items_llm(forest, {}) is None  # bascule sur le repli CAH
