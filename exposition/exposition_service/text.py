"""Normalisation du texte pour la recherche : minuscules sans accents, decoupage des noms en CamelCase
(« SaucelitoCanyonZinfandel1998 »), racinisation legere du pluriel, termes de contenu d'une question
(mots vides retires). Deterministe, sans modele."""
from __future__ import annotations

import re
import unicodedata

# Mots vides (apres repli des accents) : mots interrogatifs, determinants, prepositions, verbes courants
# de formulation (« explique », « definis »...), quelques mots anglais.
STOPWORDS = frozenset("""
a au aux avec ce ces cet cette ceci cela celui celle ceux celles comme comment dans de des du donc dont elle elles en
entre est et etre ete etait sont fait faire font peut peux peuvent plus moins tres tout tous toute toutes si ne pas
il ils je tu nous vous on la le les leur leurs lui mais me mon ma mes ni ou par pour pourquoi quand que quel quelle
quels quelles qui quoi sa se ses son sur sous sans ta te tes ton un une vers chez depuis selon apres avant pendant
y aussi ainsi alors avoir ai as avons avez ont dit dire veut vouloir signifie signifier explique expliquer expliquez
definis definir definition defini donne donner montre montrer parle parler peut-on sais savoir connais connaitre
dis moi toi quoi lequel laquelle lesquels lesquelles autre autres meme memes chaque plusieurs quelque quelques
the of and is are what how why who which this that with for from to in on at by an or
""".split())

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

_ACCENTED = {"a": "àâä", "c": "ç", "e": "éèêë", "i": "îï", "o": "ôö", "u": "ùûü", "y": "ÿ", "oe": "œ", "ae": "æ"}


def fold(text: str) -> str:
    lowered = (text or "").lower().replace("œ", "oe").replace("æ", "ae")
    return "".join(c for c in unicodedata.normalize("NFD", lowered) if unicodedata.category(c) != "Mn")


def stem(token: str) -> str:
    """Pluriel simple : « etoiles » -> « etoile », « travaux » -> « trava » (identique des deux cotes)."""
    if len(token) > 3 and token.endswith(("s", "x")):
        return token[:-1]
    return token


def tokens(text: str, split_camel: bool = False) -> list[str]:
    """Jetons replies et racinises. `split_camel` (noms d'entites, libelles de concepts) coupe d'abord
    « SaucelitoCanyonZinfandel1998 » en « saucelito canyon zinfandel 1998 »."""
    source = _CAMEL.sub(" ", text or "") if split_camel else (text or "")
    return [stem(t) for t in _NON_ALNUM.split(fold(source)) if t]


def content_terms(query: str) -> list[str]:
    """Termes porteurs de sens d'une question : sans mots vides, au moins 3 caracteres (sauf nombres),
    sans doublon, dans l'ordre."""
    seen, terms = set(), []
    for raw in _NON_ALNUM.split(fold(query)):
        if not raw or raw in STOPWORDS:
            continue
        token = stem(raw)
        if (len(token) >= 3 or token.isdigit()) and token not in seen:
            seen.add(token)
            terms.append(token)
    return terms


def cypher_folded(expr: str) -> str:
    """Expression Cypher equivalente a `fold()` : Neo4j n'a pas de normalisation, les accents francais sont
    remplaces par chaine de `replace()`."""
    out = f"toLower(coalesce({expr}, ''))"
    for base, accented in _ACCENTED.items():
        for char in accented:
            out = f"replace({out}, '{char}', '{base}')"
    return out
