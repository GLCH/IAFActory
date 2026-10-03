"""Normalisation du texte (deterministe, sans service)."""
from exposition_service.text import content_terms, fold, stem, tokens


def test_fold_removes_accents_and_case():
    assert fold("Étoile À Neutrons œuvre") == "etoile a neutrons oeuvre"


def test_stem_strips_simple_plurals_consistently():
    assert stem("etoiles") == stem("etoile") == "etoile"
    assert stem("ion") == "ion" and stem("gaz") == "gaz"  # trop court : intact


def test_tokens_split_camel_case_names_and_digits():
    assert tokens("SaucelitoCanyonZinfandel1998", split_camel=True) == ["saucelito", "canyon", "zinfandel", "1998"]
    assert tokens("SaucelitoCanyonZinfandel1998") == ["saucelitocanyonzinfandel1998"]


def test_content_terms_drop_question_words_and_keep_the_meaningful_ones():
    assert content_terms("Qu'est-ce qu'un trou noir ?") == ["trou", "noir"]
    assert content_terms("Comment une étoile meurt-elle ?") == ["etoile", "meurt"]
    assert content_terms("Quel vin accompagne un fromage ?") == ["vin", "accompagne", "fromage"]


def test_content_terms_of_a_question_without_content_is_empty():
    assert content_terms("Qu'est-ce que c'est ?") == []
    assert content_terms("   ") == []
    assert content_terms("a, le, de") == []


def test_content_terms_deduplicate_and_keep_numbers():
    assert content_terms("étoiles étoile 1998") == ["etoile", "1998"]
