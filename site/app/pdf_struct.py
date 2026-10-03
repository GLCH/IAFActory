"""Analyse structurelle minimale d'un PDF (US3.1, US3.8). Plus grossiere que
docx_struct.py : pdfplumber ne donne pas de style de titre exploitable comme
python-docx, donc chaque PAGE devient une Section (pas de sous-titres
detectes) ; chaque ligne de texte non vide devient un Paragraph (pas de
reconstruction de paragraphes multi-lignes : simplification assumee, la
reconstruction fiable d'un paragraphe a partir de lignes PDF reflowees est un
probleme non trivial, hors scope de ce vertical slice). Tableaux extraits via
pdfplumber.extract_tables().

OCR (EPIC-IAF-E18 US18.6, 2026-10-03, leve la regle "jamais d'OCR" de US3.1) : une page SCANNEE ou PHOTOGRAPHIEE
est transcrite par un modele de vision (ocr_struct.py) si `settings.ocr_enabled`, dans la limite de
`settings.ocr_max_pages` pages. Une page est scannee quand elle n'a ni texte ni tableau, OU quand une image la
couvre et que sa couche de texte est negligeable (cas reel : photos inserees dans un PDF Word, seul texte = le
numero de page). Pour une telle page la couche de texte est ecartee au profit de la transcription ; si la
transcription echoue, la couche de texte reste. Les transcriptions se font en PARALLELE
(`settings.ocr_parallelism` appels simultanes), l'ordre des pages est conserve. Sans OCR, ou si rien n'est
extrait, le PDF est refuse via ScannedDocument."""
from __future__ import annotations

import itertools
import threading
from concurrent.futures import ThreadPoolExecutor

import pdfplumber

from . import ocr_struct
from .config import settings
from .struct_element import StructElement


class ScannedDocument(ValueError):
    """PDF sans texte exploitable : scan refuse (OCR desactive) ou OCR en echec."""


def _lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def parse(path: str) -> tuple[dict, StructElement]:
    root = StructElement(kind="Document", label="racine", level=0, position=0)
    position = itertools.count(1)
    pages: list[dict] = []
    ocr_errors: list[str] = []
    ocr_skipped = 0
    futures: dict[int, object] = {}

    # Au plus 2 x parallelisme images en attente : le rendu (sequentiel, pdfplumber n'est pas thread-safe)
    # ne depasse pas la vitesse des appels au modele, ce qui borne la memoire.
    in_flight = threading.BoundedSemaphore(max(1, settings.ocr_parallelism) * 2)

    def transcribe(png: bytes) -> tuple[list[str], bool]:
        try:
            return ocr_struct.transcribe_png_checked(png)
        finally:
            in_flight.release()

    with pdfplumber.open(path) as pdf, ThreadPoolExecutor(max_workers=max(1, settings.ocr_parallelism)) as pool:
        meta = pdf.metadata or {}
        metadata = {"title": meta.get("Title"), "author": meta.get("Author"), "subject": meta.get("Subject")}

        for page_num, page in enumerate(pdf.pages, start=1):
            text_lines = _lines(page.extract_text() or "")
            tables = page.extract_tables()
            has_table_text = any(
                " | ".join(cell or "" for cell in row).strip() for table in tables for row in table
            )
            entry = {"num": page_num, "text_lines": text_lines, "tables": tables, "ocr": False}
            pages.append(entry)

            if settings.ocr_enabled and ocr_struct.needs_ocr(page, "\n".join(text_lines), has_table_text):
                if len(futures) >= settings.ocr_max_pages:
                    ocr_skipped += 1
                    continue
                try:
                    in_flight.acquire()
                    png = ocr_struct.render_page_png(page)
                except Exception as exc:  # rendu impossible : la page garde sa couche de texte
                    in_flight.release()
                    ocr_errors.append(f"page {page_num} : rendu impossible ({type(exc).__name__}: {exc})"[:200])
                    continue
                futures[page_num] = pool.submit(transcribe, png)
                entry["ocr"] = True

        ocr_lines: dict[int, list[str]] = {}
        ocr_truncated: list[int] = []
        for page_num, future in futures.items():
            try:
                ocr_lines[page_num], truncated = future.result()
                if truncated:
                    ocr_truncated.append(page_num)
            except Exception as exc:  # une page en echec n'invalide pas le document
                ocr_errors.append(f"page {page_num} : {type(exc).__name__}: {exc}"[:200])

    any_text = False
    for entry in pages:
        section = StructElement(kind="Section", label=f"Page {entry['num']}", level=1, position=next(position))
        root.children.append(section)
        # page transcrite : la transcription remplace la couche de texte (numero de page seul, en general)
        lines = ocr_lines[entry["num"]] if entry["num"] in ocr_lines else entry["text_lines"]
        for line in lines:
            any_text = True
            section.children.append(StructElement(
                kind="Paragraph", label=line[:60], level=2, position=next(position), text=line,
            ))
        for table_index, table in enumerate(entry["tables"], start=1):
            table_text = "\n".join(" | ".join(cell or "" for cell in row) for row in table)
            if not table_text.strip():
                continue
            any_text = True
            section.children.append(StructElement(
                kind="Table", label=f"Tableau {table_index} (page {entry['num']})", level=2,
                position=next(position), text=table_text,
            ))

    if ocr_lines or ocr_errors or ocr_skipped:
        metadata["ocr_pages"] = sorted(ocr_lines)
        metadata["ocr_errors"] = ocr_errors
        metadata["ocr_skipped_pages"] = ocr_skipped
        metadata["ocr_truncated_pages"] = ocr_truncated

    if not any_text:
        if ocr_errors:
            raise ScannedDocument("document scanne : OCR en echec (" + "; ".join(ocr_errors[:3]) + ")")
        raise ScannedDocument(
            "document scanne : texte non extractible (aucune couche de texte)"
            + ("" if settings.ocr_enabled else ", OCR desactive")
        )

    return metadata, root
