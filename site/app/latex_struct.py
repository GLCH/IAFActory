"""Analyse structurelle minimale d'un fichier LaTeX (US3.15). Regex simple :
pas de moteur LaTeX complet, pas d'expansion de macros, pas de gestion des
environnements imbriques complexes - simplification assumee, comme
pdf_struct.py et markdown_struct.py. `\\section`/`\\subsection`/
`\\subsubsection` deviennent des Section, `\\begin{tabular}...\\end{tabular}`
devient un Table (lignes separees par `\\\\`, cellules par `&`, best-effort),
le reste du texte (hors preambule et commandes de mise en forme isolees) est
decoupe en paragraphes par ligne vide."""
from __future__ import annotations

import itertools
import re

from .struct_element import StructElement

_SECTION_RE = re.compile(r"\\(section|subsection|subsubsection)\*?\{([^}]*)\}")
_SECTION_LEVELS = {"section": 1, "subsection": 2, "subsubsection": 3}
_TABULAR_RE = re.compile(r"\\begin\{tabular\}.*?\\end\{tabular\}", re.DOTALL)
_COMMAND_ONLY_RE = re.compile(r"^\\[a-zA-Z]+(\[[^\]]*\])?(\{[^}]*\})*\s*$")
_TITLE_RE = re.compile(r"\\title\{([^}]*)\}")
_AUTHOR_RE = re.compile(r"\\author\{([^}]*)\}")


def _clean(text: str) -> str:
    """Retire les commandes LaTeX les plus courantes pour un texte lisible ;
    ne gere pas les macros utilisateur (limite assumee)."""
    text = re.sub(r"\\(textbf|textit|emph|texttt)\{([^}]*)\}", r"\2", text)
    text = re.sub(r"%.*$", "", text, flags=re.MULTILINE)  # commentaires
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
    }

    # Ne garde que le corps du document (entre \begin{document} et
    # \end{document}) s'il existe : le preambule (macros, packages) n'est pas
    # du contenu.
    body_match = re.search(r"\\begin\{document\}(.*)\\end\{document\}", text, re.DOTALL)
    body = body_match.group(1) if body_match else text

    # Extrait les tableaux a part (pour ne pas les re-decouper en paragraphes),
    # en les remplacant par un marqueur repere par position.
    tables: list[str] = []

    def _replace_table(m: re.Match) -> str:
        tables.append(m.group(0))
        return f"\n\x00TABLE{len(tables) - 1}\x00\n"

    body = _TABULAR_RE.sub(_replace_table, body)

    root = StructElement(kind="Document", label="racine", level=0, position=0)
    stack: list[StructElement] = [root]
    position = itertools.count(1)

    # Decoupe par section d'abord (la regex de section sert de separateur),
    # en conservant l'ordre de lecture.
    parts = _SECTION_RE.split(body)
    # re.split avec groupes renvoie : [texte_avant, type1, titre1, texte1, type2, titre2, texte2, ...]
    leading_text = parts[0]
    _emit_paragraphs(leading_text, stack, position, tables)

    idx = 1
    while idx < len(parts):
        kind, title, following_text = parts[idx], parts[idx + 1], parts[idx + 2]
        level = _SECTION_LEVELS[kind]
        elem = StructElement(kind="Section", label=_clean(title), level=level, position=next(position))
        while len(stack) > 1 and stack[-1].level >= level:
            stack.pop()
        stack[-1].children.append(elem)
        stack.append(elem)
        _emit_paragraphs(following_text, stack, position, tables)
        idx += 3

    return metadata, root


def _emit_paragraphs(text: str, stack: list[StructElement], position, tables: list[str]) -> None:
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        table_marker = re.match(r"^\x00TABLE(\d+)\x00$", block)
        if table_marker:
            table_text = _table_text(tables[int(table_marker.group(1))])
            if table_text.strip():
                elem = StructElement(
                    kind="Table", label="Tableau", level=stack[-1].level + 1,
                    position=next(position), text=table_text,
                )
                stack[-1].children.append(elem)
            continue
        if _COMMAND_ONLY_RE.match(block):
            continue  # ligne de commande isolee (ex. \maketitle), pas du contenu
        cleaned = _clean(block)
        if cleaned:
            elem = StructElement(
                kind="Paragraph", label=cleaned[:60], level=stack[-1].level + 1,
                position=next(position), text=cleaned,
            )
            stack[-1].children.append(elem)
