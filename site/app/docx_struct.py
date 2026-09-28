"""Analyse structurelle minimale d'un .docx : produit un arbre d'elements
types (StructElement, struct_element.py), en ordre de lecture. Porte depuis
poc/docx_struct.py (preuve de bout en bout, 2026-09-27). Remplace Docling
(US3.8, non evalue ici) pour ce vertical slice : ne gere que titres
(Heading 1..9), paragraphes et tableaux simples, sans figures ni notes."""
from __future__ import annotations

import itertools

from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from .struct_element import StructElement


def _iter_block_items(document: DocxDocument):
    """Paragraphes et tableaux dans l'ordre du document (python-docx ne le
    fournit pas nativement : recette classique via l'arbre XML du corps)."""
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _table_text(table: Table) -> str:
    lines = []
    for row in table.rows:
        lines.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(lines)


def parse(path: str) -> tuple[dict, StructElement]:
    document = DocxDocument(path)
    core = document.core_properties
    metadata = {"title": core.title, "author": core.author, "subject": core.subject}

    root = StructElement(kind="Document", label="racine", level=0, position=0)
    stack: list[StructElement] = [root]
    position = itertools.count(1)

    for block in _iter_block_items(document):
        if isinstance(block, Paragraph):
            style = (block.style.name or "") if block.style else ""
            text = block.text.strip()
            if style.startswith("Heading") or style.startswith("Title"):
                level = 0 if style.startswith("Title") else int(style.rsplit(" ", 1)[-1] or 1)
                level = max(level, 1)
                elem = StructElement(kind="Section", label=text, level=level, position=next(position))
                while len(stack) > 1 and stack[-1].level >= level:
                    stack.pop()
                stack[-1].children.append(elem)
                stack.append(elem)
            elif text:
                elem = StructElement(kind="Paragraph", label=text[:60], level=stack[-1].level + 1,
                                      position=next(position), text=text)
                stack[-1].children.append(elem)
        elif isinstance(block, Table):
            text = _table_text(block)
            label = f"Tableau ({len(block.rows)}x{len(block.columns)})"
            elem = StructElement(kind="Table", label=label, level=stack[-1].level + 1,
                                  position=next(position), text=text)
            stack[-1].children.append(elem)

    return metadata, root
