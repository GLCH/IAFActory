"""Reproduit exactement la logique de POST /admin/wipe (routers/admin.py),
invoquee hors HTTP (pas de session admin connectee disponible depuis ce
script). Demande explicite de l'utilisateur, meme perimetre que la route :
Postgres (documents/pipeline) + Neo4j (tout) + Fuseki, PAS les comptes User.

Usage : python scripts/wipe_data.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete as sa_delete  # noqa: E402

from app import ontology  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.graph import get_driver  # noqa: E402
from app.models import Document, PipelineRun, PipelineStep  # noqa: E402


def main() -> None:
    driver = get_driver()
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    print("Neo4j : tous les noeuds/relations supprimes")

    removed = 0
    if settings.documents_dir.exists():
        for path in settings.documents_dir.iterdir():
            if path.is_file():
                path.unlink()
                removed += 1
    print(f"Fichiers documents supprimes : {removed}")

    with SessionLocal() as db:
        db.execute(sa_delete(PipelineStep))
        db.execute(sa_delete(PipelineRun))
        db.execute(sa_delete(Document))
        db.commit()
    print("Postgres : PipelineStep/PipelineRun/Document vides (comptes User conserves)")

    try:
        ontology.wipe_all()
        print("Fuseki : wipe_all() OK")
    except Exception as exc:  # meme comportement que la route (try/except silencieux)
        print(f"Fuseki : wipe_all() a echoue (ignore comme dans la route) : {exc}")


if __name__ == "__main__":
    main()
