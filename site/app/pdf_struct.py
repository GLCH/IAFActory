"""Analyse structurelle minimale d'un PDF (US3.1, US3.8). Plus grossiere que
docx_struct.py : pdfplumber ne donne pas de style de titre exploitable comme
python-docx, donc chaque PAGE devient une Section (pas de sous-titres
detectes) ; chaque ligne de texte non vide devient un Paragraph (pas de
reconstruction de paragraphes multi-lignes : simplification assumee, la
reconstruction fiable d'un paragraphe a partir de lignes PDF reflowees est un
probleme non trivial, hors scope de ce vertical slice). Tableaux extraits via
pdfplumber.extract_tables(). Aucun OCR (US3.1) : un PDF sans aucune ligne de
texte extractible sur aucune page est refuse via ScannedDocument."""
from __future__ import annotations

import itertools

import pdfplumber

from .struct_element import StructElement


class ScannedDocument(ValueError):
    """PDF sans couche de texte extractible sur aucune page (scan). US3.1 :
    refus explicite, jamais d'OCR."""


def parse(path: str) -> tuple[dict, StructElement]:
    root = StructElement(kind="Document", label="racine", level=0, position=0)
    position = itertools.count(1)
    any_text = False

    with pdfplumber.open(path) as pdf:
        meta = pdf.metadata or {}
        metadata = {"title": meta.get("Title"), "author": meta.get("Author"), "subject": meta.get("Subject")}

        for page_num, page in enumerate(pdf.pages, start=1):
            section = StructElement(
                kind="Section", label=f"Page {page_num}", level=1, position=next(position),
            )
            root.children.append(section)

            text = page.extract_text() or ""
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                any_text = True
                elem = StructElement(
                    kind="Paragraph", label=line[:60], level=2, position=next(position), text=line,
                )
                section.children.append(elem)

            for table_index, table in enumerate(page.extract_tables(), start=1):
                rows = [" | ".join(cell or "" for cell in row) for row in table]
                table_text = "\n".join(rows)
                if not table_text.strip():
                    continue
                any_text = True
                elem = StructElement(
                    kind="Table", label=f"Tableau {table_index} (page {page_num})", level=2,
                    position=next(position), text=table_text,
                )
                section.children.append(elem)

    if not any_text:
        raise ScannedDocument("document scanne : texte non extractible (aucune couche de texte), aucun OCR effectue")

    return metadata, root
