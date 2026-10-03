"""Mesure l'OCR (EPIC-IAF-E18 US18.6) sur un PDF scanne dont on connait le texte exact.

Compare, page par page, les lignes transcrites aux lignes de verite terrain (celles de
make_scanned_sample.PAGES) : taux de lignes identiques apres normalisation (casse, espaces)
et similarite moyenne de caracteres (difflib, 1.0 = identique). Appelle le VRAI modele de
vision : cout et duree reels (un appel par page).

Usage (depuis site/) :
  python scripts/measure_ocr.py ../corpus/documents/scannes/note-apiculture-manuscrite.pdf
"""
from __future__ import annotations

import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import pdf_struct  # noqa: E402
from make_scanned_sample import PAGES  # noqa: E402


def norm(text: str) -> str:
    return " ".join(text.lower().split())


def main() -> None:
    path = sys.argv[1]
    metadata, root = pdf_struct.parse(path)
    print("metadonnees OCR :", {k: v for k, v in metadata.items() if k.startswith("ocr")})
    total, exact, ratios = 0, 0, []
    for page_index, section in enumerate(root.children):
        transcribed = [child.text for child in section.children]
        truth = PAGES[page_index] if page_index < len(PAGES) else []
        print(f"\n--- page {page_index + 1} : {len(transcribed)} ligne(s) transcrite(s), {len(truth)} attendue(s)")
        for expected in truth:
            best = max(transcribed, key=lambda t: difflib.SequenceMatcher(None, norm(t), norm(expected)).ratio(), default="")
            ratio = difflib.SequenceMatcher(None, norm(best), norm(expected)).ratio()
            total += 1
            exact += 1 if norm(best) == norm(expected) else 0
            ratios.append(ratio)
            print(f"  {ratio:.2f} | attendu : {expected}\n       | lu      : {best}")
    if total:
        print(f"\nlignes identiques : {exact}/{total} ; similarite moyenne de caracteres : {sum(ratios) / total:.3f}")


if __name__ == "__main__":
    main()
