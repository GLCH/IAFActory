"""Analyse structurelle minimale d'un fichier Markdown (US3.15). Ligne par
ligne, regex simple : pas de bibliotheque Markdown complete (pas de gestion
des listes imbriquees, blocs de code multi-lignes traites comme paragraphe
brut, emphases/liens laisses tels quels dans le texte) - simplification
assumee, comme pdf_struct.py."""
from __future__ import annotations

import itertools
import re

from .struct_element import StructElement

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
_TABLE_SEP_RE = re.compile(r"^\|[\s:|-]+\|$")


def parse(path: str) -> tuple[dict, StructElement]:
    text = open(path, encoding="utf-8", errors="replace").read()
    lines = text.splitlines()

    root = StructElement(kind="Document", label="racine", level=0, position=0)
    stack: list[StructElement] = [root]
    position = itertools.count(1)

    i = 0
    paragraph_buffer: list[str] = []

    def flush_paragraph():
        if not paragraph_buffer:
            return
        joined = " ".join(paragraph_buffer).strip()
        paragraph_buffer.clear()
        if joined:
            elem = StructElement(
                kind="Paragraph", label=joined[:60], level=stack[-1].level + 1,
                position=next(position), text=joined,
            )
            stack[-1].children.append(elem)

    while i < len(lines):
        line = lines[i]
        heading = _HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            elem = StructElement(kind="Section", label=title, level=level, position=next(position))
            while len(stack) > 1 and stack[-1].level >= level:
                stack.pop()
            stack[-1].children.append(elem)
            stack.append(elem)
            i += 1
            continue

        if _TABLE_ROW_RE.match(line) and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1].strip()):
            flush_paragraph()
            rows = [line]
            j = i + 2
            while j < len(lines) and _TABLE_ROW_RE.match(lines[j]):
                rows.append(lines[j])
                j += 1
            # `rows` = ligne d'entete + lignes de donnees ; la ligne de
            # separation ---|--- n'y a jamais ete ajoutee (boucle demarree a
            # i+2, juste apres elle) - bug reel corrige : un premier essai
            # retirait a tort `rows[1]` (la premiere ligne de donnees).
            table_text = "\n".join(r.strip().strip("|").strip() for r in rows)
            elem = StructElement(
                kind="Table", label=f"Tableau ({len(rows)} lignes)", level=stack[-1].level + 1,
                position=next(position), text=table_text,
            )
            stack[-1].children.append(elem)
            i = j
            continue

        if not line.strip():
            flush_paragraph()
            i += 1
            continue

        paragraph_buffer.append(line.strip())
        i += 1

    flush_paragraph()

    # Pas de metadonnees fiables dans un .md brut (pas de front-matter analyse
    # ici) : champs vides, jamais inventes (meme principe que US3.13).
    metadata = {"title": None, "author": None, "subject": None}
    return metadata, root
