"""Genere des documents d'exemple par lot, a partir d'un fichier de
configuration YAML (IAF-E16 US16.4). Recombine le contenu REEL deja present
dans l'ontologie structurelle et semantique de chaque classe (Neo4j) - aucun
appel LLM, aucun texte invente (voir app/doc_generator.py et
docs/epics/EPIC-IAF-E16-generation-documents-exemple.md pour les decisions de
cadrage completes).

Usage : python scripts/generate_documents.py <config.yaml>
Voir generate_documents.example.yaml pour le format attendu.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from app.doc_generator import (  # noqa: E402
    ALLOWED_VOCABULARY_SIZES, DEFAULT_VOCABULARY_SIZE, DEFAULT_WORDS, STRUCTURE_STYLES,
    generate_document, load_class_material, write_docx, write_markdown, write_pdf,
)
from app.graph import get_driver  # noqa: E402

WRITERS = {"md": write_markdown, "docx": write_docx, "pdf": write_pdf}


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python scripts/generate_documents.py <config.yaml>")
    config_path = Path(sys.argv[1])
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not config or "classes" not in config:
        raise SystemExit("config invalide : cle 'classes' manquante")

    output_dir = Path(config.get("output_dir", "./data/generated"))
    output_dir.mkdir(parents=True, exist_ok=True)
    seed = config.get("seed")

    driver = get_driver()
    manifest: list[dict] = []
    n_written = 0
    all_warnings: list[str] = []

    with driver.session() as session:
        for entry in config["classes"]:
            class_id = entry["class_id"]
            count = int(entry.get("count", 1))
            words = int(entry.get("words", DEFAULT_WORDS))
            vocabulary_size = int(entry.get("vocabulary_size", DEFAULT_VOCABULARY_SIZE))
            formats = entry.get("formats", ["md"])
            structure = entry.get("structure", "flat")

            if vocabulary_size not in ALLOWED_VOCABULARY_SIZES:
                raise SystemExit(
                    f"classe {class_id} : vocabulary_size doit etre l'un de {ALLOWED_VOCABULARY_SIZES}, "
                    f"recu {vocabulary_size}"
                )
            if structure not in STRUCTURE_STYLES:
                raise SystemExit(f"classe {class_id} : structure doit etre l'un de {STRUCTURE_STYLES}, recu {structure!r}")
            unknown_formats = set(formats) - set(WRITERS)
            if unknown_formats:
                raise SystemExit(
                    f"classe {class_id} : format(s) non pris en charge dans cette version : "
                    f"{sorted(unknown_formats)} (disponibles : {sorted(WRITERS)} - voir US16.5, non fait)"
                )

            try:
                material = load_class_material(session, class_id)
            except ValueError as exc:
                print(f"[ignore] {exc}")
                continue

            class_dir = output_dir / class_id
            class_dir.mkdir(parents=True, exist_ok=True)

            for i in range(count):
                # Graine derivee (classe, index) pour que chaque document d'un
                # meme lot soit different mais que le lot entier reste
                # reproductible a graine globale egale.
                doc_seed = None if seed is None else hash((seed, class_id, i)) & 0xFFFFFFFF
                doc = generate_document(
                    material, words=words, vocabulary_size=vocabulary_size, seed=doc_seed, structure=structure,
                )

                written_files = []
                for fmt in formats:
                    path = class_dir / f"{class_id}-{i + 1:03d}.{fmt}"
                    WRITERS[fmt](doc, path)
                    written_files.append(str(path))

                n_written += 1
                all_warnings.extend(doc.warnings)
                manifest.append({
                    "class_id": class_id,
                    "class_name": material.class_name,
                    "index": i + 1,
                    "files": written_files,
                    "requested_words": doc.requested_words,
                    "actual_words": doc.word_count,
                    "warnings": doc.warnings,
                    "ground_truth": doc.ground_truth,
                })
                print(f"{class_id} #{i + 1}/{count} : {doc.word_count}/{words} mots, {len(written_files)} fichier(s)")

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{n_written} document(s) genere(s) dans {output_dir}")
    print(f"manifeste (verite terrain, pour US7.7) : {manifest_path}")
    if all_warnings:
        print(f"{len(all_warnings)} avertissement(s) (plafonnement honnete ou materiau reel insuffisant) "
              "- voir le manifeste")


if __name__ == "__main__":
    main()
