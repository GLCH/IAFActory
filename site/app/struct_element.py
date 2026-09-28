"""Arbre d'elements structurels partage par les trois analyseurs de format
(docx_struct.py, pdf_struct.py, pptx_struct.py). Vocabulaire simplifie de
ontologies/structure/iaf-structure-base.ttl (US3.8) : Section, Paragraph,
Table uniquement pour ce vertical slice (pas de figure, note, legende)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class StructElement:
    kind: str  # Section | Paragraph | Table
    label: str
    level: int
    position: int
    text: str = ""  # contenu textuel utilise pour le chunk (vide pour Section)
    children: list["StructElement"] = field(default_factory=list)
    # uuid4, jamais un compteur de process partage entre documents/appels
    # (bug reel rencontre le 2026-09-27 : un compteur "se1, se2..." redemarre
    # a chaque analyse et entre en collision avec un document precedent dans
    # le meme Neo4j persistant).
    id: str = field(default_factory=lambda: f"se-{uuid.uuid4().hex}")


def flatten(elem: StructElement) -> list[StructElement]:
    out = [elem] if elem.kind != "Document" else []
    for child in elem.children:
        out.extend(flatten(child))
    return out
