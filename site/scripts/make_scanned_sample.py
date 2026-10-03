"""Fabrique un PDF "scanne manuscrit" SIMULE pour tester l'OCR (EPIC-IAF-E18 US18.6).

Chaque page est une image (papier legerement bruite, lignes ecrites avec une police
manuscrite, petite rotation) enregistree dans un PDF SANS couche de texte : pdfplumber n'y
trouve aucun texte, le document passe donc par l'OCR. ATTENTION : une police manuscrite
n'est pas une vraie main (traits reguliers, lettres constantes) ; la fiabilite sur une vraie
ecriture doit etre mesuree avec un vrai scan fourni par l'utilisateur.

Usage (depuis site/) :
  python scripts/make_scanned_sample.py --out ../corpus/documents/scannes/note-apiculture-manuscrite.pdf
  python scripts/make_scanned_sample.py --out x.pdf --font C:/Windows/Fonts/segoesc.ttf --seed 3
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

PAGES = [
    [
        "Note de visite - rucher de la Combe",
        "Introduction",
        "Visite du 14 mai : etat general des colonies apres l'hiver.",
        "La reine pond bien, le couvain est regulier et compact.",
        "Les abeilles butinent les fleurs de pissenlit et de colza.",
        "Le miel de printemps sera recolte fin mai.",
    ],
    [
        "Observations",
        "Ruche 3 : essaimage probable, trois cellules royales ouvertes.",
        "Ruche 5 : presence de varroa, traitement a l'acide oxalique prevu.",
        "Ruche 7 : faible, reunir avec la ruche 6 si la reine est absente.",
        "Conclusion",
        "Revenir dans dix jours pour poser les hausses.",
    ],
]


def render_page(lines: list[str], font_path: str, rng: random.Random, size=(1240, 1754)) -> Image.Image:
    page = Image.new("L", size, 238)
    draw = ImageDraw.Draw(page)
    # bruit de papier
    for _ in range(9000):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        draw.point((x, y), fill=rng.randrange(205, 245))
    font = ImageFont.truetype(font_path, 36)
    y = 150
    for line in lines:
        layer = Image.new("L", size, 255)
        ImageDraw.Draw(layer).text((110 + rng.randrange(-8, 9), y + rng.randrange(-6, 7)), line, font=font, fill=rng.randrange(20, 70))
        page = Image.fromarray(np.minimum(np.array(page), np.array(layer)))  # encre = pixel le plus sombre
        y += 110
    return page.rotate(rng.uniform(-1.2, 1.2), resample=Image.BICUBIC, fillcolor=238)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True)
    parser.add_argument("--font", default="C:/Windows/Fonts/Inkfree.ttf")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    images = [render_page(lines, args.font, rng).convert("RGB") for lines in PAGES]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(str(out), "PDF", resolution=150.0, save_all=True, append_images=images[1:])
    print(f"{out} : {len(images)} page(s) image, sans couche de texte")


if __name__ == "__main__":
    main()
