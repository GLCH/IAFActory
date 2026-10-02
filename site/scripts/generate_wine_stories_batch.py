"""Genere N documents de chaque type (recit .docx + brochure .pdf) en lot,
en reutilisant les fonctions de generate_wine_stories.py sans dupliquer la
logique. Demande explicite (2026-10-01) : "genere 10 documents de chaque
type."

Rotation sur le pool reel de vins (53 vins identifies, voir
generate_wine_stories.py:real_wine_names) pour varier les triplets de vins
d'un recit a l'autre et le vin choisi pour chaque brochure - jamais le meme
ordre fige, mais aucune invention : chaque document individuel reste ancre
sur du materiau 100% reel pour sa partie "fiche", comme les scripts qu'il
reutilise.

Usage : python scripts/generate_wine_stories_batch.py <class_id> <output_dir> [--count 10] [--language fr]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.graph import get_driver  # noqa: E402
from app.doc_generator import load_class_material  # noqa: E402
from app.doc_generator import write_docx, write_pdf  # noqa: E402
from generate_wine_stories import build_docx_document, build_pdf_document, real_wine_names  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("class_id")
    parser.add_argument("output_dir")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--language", default="fr")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    driver = get_driver()
    with driver.session() as session:
        material = load_class_material(session, args.class_id)

    wines = real_wine_names(material)
    if len(wines) < 3:
        raise SystemExit(f"materiau insuffisant : {len(wines)} vin(s) reel(s) identifie(s), 3 minimum requis")
    print(f"{len(wines)} vins reels disponibles pour la rotation\n")

    for i in range(args.count):
        triplet = [wines[(i * 3 + k) % len(wines)] for k in range(3)]
        print(f"--- recit {i + 1}/{args.count} : {triplet} ---")
        doc = build_docx_document(material, triplet, args.language)
        path = out_dir / f"vin-recit-{i + 1:02d}.docx"
        write_docx(doc, path)
        print(f"  ecrit : {path}\n")

    for i in range(args.count):
        wine = wines[(i * 7) % len(wines)]  # pas 7 : evite de retomber sur le meme ordre que les recits
        print(f"--- brochure {i + 1}/{args.count} : {wine} ---")
        doc = build_pdf_document(material, wine, args.language)
        path = out_dir / f"vin-brochure-{i + 1:02d}.pdf"
        write_pdf(doc, path)
        print(f"  ecrit : {path}\n")

    print(f"{args.count} recit(s) .docx et {args.count} brochure(s) .pdf ecrits dans {out_dir}")


if __name__ == "__main__":
    main()
