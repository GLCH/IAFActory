"""Analyse structurelle d'un fichier LaTeX (US3.15, etendu le 2026-09-28 -
addendum : bug reel constate a l'usage, un document scientifique reel n'a
fait reconnaitre que tres peu de termes). Toujours une regex simple, pas un
moteur LaTeX complet (limite assumee, comme pdf_struct.py/markdown_struct.py),
mais qui lit maintenant la vraie terminologie LaTeX d'un document
scientifique plutot que les seules sections et tableaux :
- `\\section`/`\\subsection`/`\\subsubsection` -> Section (inchange) ;
- `\\begin{tabular}...\\end{tabular}` -> Table (inchange) ;
- environnements demonstratifs (theorem/lemma/proposition/corollary/
  definition/proof/algorithm) -> Section, le nom de l'environnement EST le
  libelle (ex. "Theoreme (convergence)") ;
- environnements mathematiques (equation/align/gather/multline/eqnarray,
  `$$...$$`, `\\[...\\]`) -> nouveau type "Equation" (STRUCT_KINDS,
  pipeline.py), le contenu est TRADUIT terme a terme (`_MATH_WORDS`) plutot
  que supprime.

Cause du bug corrige ici : `_clean()` supprimait TOUTES les commandes LaTeX
sans distinction, y compris les maths INLINE dans le texte courant
(`$\\theta$` devenait `$$`, un texte vide) - un document plein de notation
mathematique (papier ML/maths) se retrouvait avec un texte quasi vide apres
nettoyage, donc quasiment aucun terme a extraire (US7.1). `_clean()` traduit
maintenant les maths inline avant de nettoyer le reste."""
from __future__ import annotations

import itertools
import re

from .struct_element import StructElement

_SECTION_RE = re.compile(r"\\(section|subsection|subsubsection)\*?\{([^}]*)\}")
_SECTION_LEVELS = {"section": 1, "subsection": 2, "subsubsection": 3}
_TABULAR_RE = re.compile(r"\\begin\{tabular\}.*?\\end\{tabular\}", re.DOTALL)

_BLOCK_ENVS = ("theorem", "lemma", "proposition", "corollary", "definition", "proof", "algorithm", "algorithmic")
_BLOCK_ENV_RE = re.compile(
    r"\\begin\{(" + "|".join(_BLOCK_ENVS) + r")\*?\}(?:\[([^\]]*)\])?(.*?)\\end\{\1\*?\}", re.DOTALL,
)
_BLOCK_LABELS = {
    "theorem": "Theoreme", "lemma": "Lemme", "proposition": "Proposition",
    "corollary": "Corollaire", "definition": "Definition", "proof": "Preuve",
    "algorithm": "Algorithme", "algorithmic": "Algorithme",
}

_MATH_ENVS = ("equation", "align", "gather", "multline", "eqnarray")
_MATH_ENV_RE = re.compile(
    r"\\begin\{(" + "|".join(_MATH_ENVS) + r")\*?\}(.*?)\\end\{\1\*?\}", re.DOTALL,
)
_DISPLAY_MATH_RE = re.compile(r"\\\[(.*?)\\\]|\$\$(.*?)\$\$", re.DOTALL)
_INLINE_MATH_RE = re.compile(r"\$([^$]*)\$|\\\((.*?)\\\)", re.DOTALL)

_COMMAND_ONLY_RE = re.compile(r"^\\[a-zA-Z]+(\[[^\]]*\])?(\{[^}]*\})*\s*$")
_TITLE_RE = re.compile(r"\\title\{([^}]*)\}")
_AUTHOR_RE = re.compile(r"\\author\{([^}]*)\}")
_CITE_RE = re.compile(r"\\cite[tp]?\{([^}]*)\}")

# Traduction des commandes mathematiques les plus courantes (notation ->
# mot lisible en francais) pour que le contenu mathematique/ML soit
# effectivement present dans le texte transmis a l'extraction de vocabulaire
# (US7.1), au lieu d'etre supprime. Liste fermee et non exhaustive - assumee,
# pas une expansion generale de macros LaTeX (ADR implicite : simplicite avant
# fidelite totale, comme le reste de ce module).
_MATH_WORDS = {
    "alpha": "alpha", "beta": "beta", "gamma": "gamma", "delta": "delta",
    "epsilon": "epsilon", "zeta": "zeta", "eta": "eta", "theta": "theta",
    "iota": "iota", "kappa": "kappa", "lambda": "lambda", "mu": "mu", "nu": "nu",
    "xi": "xi", "pi": "pi", "rho": "rho", "sigma": "sigma", "Sigma": "Sigma",
    "tau": "tau", "phi": "phi", "chi": "chi", "psi": "psi", "omega": "omega",
    "nabla": "gradient", "partial": "derivee partielle", "sum": "somme",
    "prod": "produit", "int": "integrale", "infty": "infini",
    "log": "logarithme", "exp": "exponentielle", "min": "minimum", "max": "maximum",
    "argmax": "argmax", "argmin": "argmin", "sim": "suit la loi",
    "approx": "approximativement egal a", "propto": "proportionnel a",
    "forall": "pour tout", "exists": "il existe", "in": "appartient a",
    "notin": "n'appartient pas a", "subset": "sous-ensemble de",
    "cup": "union", "cap": "intersection", "setminus": "prive de",
    "rightarrow": "vers", "to": "vers", "Rightarrow": "implique",
    "leftrightarrow": "equivaut a", "times": "fois", "cdot": "fois",
    "leq": "inferieur ou egal a", "geq": "superieur ou egal a", "neq": "different de",
    "pm": "plus ou moins", "hat": "estimateur", "bar": "moyenne", "tilde": "approximation",
    "top": "transpose", "circ": "compose avec", "otimes": "produit tensoriel",
    "langle": "", "rangle": "", "vert": "", "Vert": "norme",
    "text": "", "quad": " ", "qquad": " ",
}


def _translate_math(fragment: str) -> str:
    """Traduit un fragment de maths LaTeX (interieur de `$...$`, d'un
    environnement equation/align/..., etc.) en mots lisibles - best-effort,
    pas un rendu mathematique fidele."""
    fragment = re.sub(r"\\(mathbb|mathcal|mathrm|mathbf|operatorname)\{([^}]*)\}", r"\2", fragment)

    def _word(m: re.Match) -> str:
        name = m.group(1)
        return _MATH_WORDS.get(name, name)  # commande inconnue : garde le nom (souvent significatif, ex. macro utilisateur)

    fragment = re.sub(r"\\([a-zA-Z]+)", _word, fragment)
    fragment = fragment.replace(r"\|", " norme ")  # barres de norme, non couvertes par la regle de mot ci-dessus
    fragment = re.sub(r"[_^{}]", " ", fragment)
    fragment = fragment.replace("\\", "")  # symboles restants non traduits (ex. \{, \\) : mieux absents que des barres obliques isolees
    return re.sub(r"\s+", " ", fragment).strip()


def _clean(text: str) -> str:
    """Retire les commandes LaTeX les plus courantes pour un texte lisible ;
    traduit d'abord les maths inline (cf. docstring du module - c'etait le
    bug reel : supprimees avant, laissant un texte vide)."""
    text = re.sub(r"\\(textbf|textit|emph|texttt)\{([^}]*)\}", r"\2", text)
    text = re.sub(r"%.*$", "", text, flags=re.MULTILINE)  # commentaires
    text = _CITE_RE.sub(lambda m: f"(ref. {m.group(1)})", text)
    text = _INLINE_MATH_RE.sub(lambda m: _translate_math(m.group(1) or m.group(2) or ""), text)
    text = re.sub(r"\\[a-zA-Z]+(\[[^\]]*\])?", "", text)  # commandes restantes
    text = re.sub(r"[{}]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _table_text(tabular_block: str) -> str:
    inner = re.sub(r"\\begin\{tabular\}\{[^}]*\}", "", tabular_block)
    inner = inner.replace("\\end{tabular}", "").replace("\\hline", "")
    rows = []
    for line in inner.split("\\\\"):
        line = line.strip()
        if not line:
            continue
        cells = [_clean(c) for c in line.split("&")]
        rows.append(" | ".join(c for c in cells if c))
    return "\n".join(r for r in rows if r)


def parse(path: str) -> tuple[dict, StructElement]:
    text = open(path, encoding="utf-8", errors="replace").read()

    title_match = _TITLE_RE.search(text)
    author_match = _AUTHOR_RE.search(text)
    metadata = {
        "title": _clean(title_match.group(1)) if title_match else None,
        "author": _clean(author_match.group(1)) if author_match else None,
        "subject": None,
        # Ajoute le 2026-09-29 (demande explicite : "organisation du contenu
        # - section, subsection, ..., citations, theorems, definitions" pour
        # le matching STRUCTUREL) - densite de citations, pas une proportion
        # STRUCT_KINDS de plus (une citation n'est pas un bloc de contenu au
        # meme titre qu'un paragraphe/tableau/equation, c'est un marqueur
        # inline - voir pipeline.py:_structural_profile).
        "citation_count": len(_CITE_RE.findall(text)),
    }

    # Ne garde que le corps du document (entre \begin{document} et
    # \end{document}) s'il existe : le preambule (macros, packages) n'est pas
    # du contenu.
    body_match = re.search(r"\\begin\{document\}(.*)\\end\{document\}", text, re.DOTALL)
    body = body_match.group(1) if body_match else text

    # Extrait tableaux, environnements mathematiques et blocs demonstratifs a
    # part (pour ne pas les re-decouper en paragraphes ni perdre leur
    # contenu), chacun remplace par un marqueur repere par position.
    tables: list[str] = []
    equations: list[str] = []
    blocks: list[tuple[str, str, str]] = []  # (env, titre_optionnel, contenu)

    def _replace_table(m: re.Match) -> str:
        tables.append(m.group(0))
        return f"\n\x00TABLE{len(tables) - 1}\x00\n"

    def _replace_math_env(m: re.Match) -> str:
        equations.append(m.group(2))
        return f"\n\x00EQ{len(equations) - 1}\x00\n"

    def _replace_display_math(m: re.Match) -> str:
        equations.append(m.group(1) or m.group(2) or "")
        return f"\n\x00EQ{len(equations) - 1}\x00\n"

    def _replace_block(m: re.Match) -> str:
        blocks.append((m.group(1), m.group(2) or "", m.group(3)))
        return f"\n\x00BLOCK{len(blocks) - 1}\x00\n"

    body = _TABULAR_RE.sub(_replace_table, body)
    body = _MATH_ENV_RE.sub(_replace_math_env, body)
    body = _DISPLAY_MATH_RE.sub(_replace_display_math, body)
    body = _BLOCK_ENV_RE.sub(_replace_block, body)

    root = StructElement(kind="Document", label="racine", level=0, position=0)
    stack: list[StructElement] = [root]
    position = itertools.count(1)

    def _emit_paragraphs(text: str, stack: list[StructElement]) -> None:
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if not para:
                continue
            table_marker = re.match(r"^\x00TABLE(\d+)\x00$", para)
            if table_marker:
                table_text = _table_text(tables[int(table_marker.group(1))])
                if table_text.strip():
                    stack[-1].children.append(StructElement(
                        kind="Table", label="Tableau", level=stack[-1].level + 1,
                        position=next(position), text=table_text,
                    ))
                continue
            eq_marker = re.match(r"^\x00EQ(\d+)\x00$", para)
            if eq_marker:
                eq_text = _translate_math(equations[int(eq_marker.group(1))])
                if eq_text:
                    stack[-1].children.append(StructElement(
                        kind="Equation", label=eq_text[:60], level=stack[-1].level + 1,
                        position=next(position), text=eq_text,
                    ))
                continue
            block_marker = re.match(r"^\x00BLOCK(\d+)\x00$", para)
            if block_marker:
                env, title, inner = blocks[int(block_marker.group(1))]
                label = _BLOCK_LABELS.get(env, env.capitalize())
                if title:
                    label = f"{label} ({_clean(title)})"
                block_elem = StructElement(
                    kind="Section", label=label, level=stack[-1].level + 1, position=next(position),
                )
                stack[-1].children.append(block_elem)
                stack.append(block_elem)
                _emit_paragraphs(inner, stack)
                stack.pop()
                continue
            if _COMMAND_ONLY_RE.match(para):
                continue  # ligne de commande isolee (ex. \maketitle), pas du contenu
            cleaned = _clean(para)
            if cleaned:
                stack[-1].children.append(StructElement(
                    kind="Paragraph", label=cleaned[:60], level=stack[-1].level + 1,
                    position=next(position), text=cleaned,
                ))

    # Decoupe par section d'abord (la regex de section sert de separateur),
    # en conservant l'ordre de lecture.
    parts = _SECTION_RE.split(body)
    # re.split avec groupes renvoie : [texte_avant, type1, titre1, texte1, type2, titre2, texte2, ...]
    _emit_paragraphs(parts[0], stack)

    idx = 1
    while idx < len(parts):
        kind, title, following_text = parts[idx], parts[idx + 1], parts[idx + 2]
        level = _SECTION_LEVELS[kind]
        elem = StructElement(kind="Section", label=_clean(title), level=level, position=next(position))
        while len(stack) > 1 and stack[-1].level >= level:
            stack.pop()
        stack[-1].children.append(elem)
        stack.append(elem)
        _emit_paragraphs(following_text, stack)
        idx += 3

    return metadata, root
