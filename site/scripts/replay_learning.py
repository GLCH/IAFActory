"""Rejoue un SCENARIO d'apprentissage de classes (jeu d'essai du depot, dossier
`corpus/`) contre le site reel : classes de depart (seeds), depot des documents
UN PAR UN par la vraie route du site (`POST /creator/documents/new`), attente
du worker et du cycle de vie (EPIC-IAF-E17), trace de chaque decision.

Pourquoi par la vraie route : un appel direct a `pipeline.ingest_document()`
laisse Neo4j et Postgres divergents (constate le 2026-10-02) ; ici le chemin est
exactement celui d'un creator.

Usage (depuis site/, venv du site, infra demarree, site reconstruit) :
  python scripts/replay_learning.py ../corpus/scenarios/apprentissage-classe-inconnue.yaml --seed
  python scripts/replay_learning.py <scenario> --dry-run          # liste ce qui serait fait
  python scripts/replay_learning.py <scenario> --reset-learned --yes   # repart d'un etat propre

Variables d'environnement : REPLAY_EMAIL, REPLAY_PASSWORD (compte creator ou
admin du site, jamais en dur ici), REPLAY_BASE_URL (defaut http://localhost:8010).
Les ports Neo4j/Fuseki/Postgres viennent du `.env` racine, comme les autres scripts.

Format du scenario (YAML) :
  name, description
  seeds: [Vin, C2SIM]              # classes de depart, definies dans corpus/classes.yaml
  steps:
    - document: documents/inconnus/astro/astro-1.docx
      note: "texte libre, resultat observe a l'enregistrement du scenario"
    - action: reevaluate           # bouton "Reevaluer les classes inconnues"

`--reset-learned` supprime (routes de suppression du site, cascade complete) les
classes NON seedees (statut different de `exemple-importe`, coquilles fusionnees incluses) puis les documents
du scenario encore presents ; il exige `--yes`. Sans `--reset-learned`, un
document deja depose (meme sha256) est refuse par le site (409) et signale."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402
import yaml  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.graph import get_driver  # noqa: E402
from app.models import Document, PipelineRun, PipelineStep  # noqa: E402

SITE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SITE_ROOT.parent
CORPUS_ROOT = REPO_ROOT / "corpus"
SEED_STATUS = "exemple-importe"
CONTENT_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".tex": "application/x-tex",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
TRACED_LABELS = ("OCR", "Reconnaissance", "Pre-filtrage", "Recherche de corpus", "Similarite", "Fusion", "Promotion", "Classe provisoire", "Rattachement")


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def class_rows(include_merged: bool = False) -> list[dict]:
    """Classes vivantes ; `include_merged` ajoute les coquilles `fusionnee_*` (classes absorbees,
    gardees pour la redirection des anciennes URL)."""
    where = "" if include_merged else "WHERE NOT coalesce(c.status, '') STARTS WITH 'fusionnee' "
    with get_driver().session() as session:
        return [dict(r) for r in session.run(
            "MATCH (c:DocumentClass) " + where +
            "OPTIONAL MATCH (c)<-[:IN_CLASS]-(d:Document) "
            "RETURN c.id AS id, c.name AS name, coalesce(c.status, '') AS status, count(DISTINCT d) AS docs ORDER BY c.name"
        )]


def print_state() -> None:
    for row in class_rows():
        print(f"  classe: {row['name']} [{row['status']}] {row['docs']} document(s)", flush=True)


def ensure_seeds(seed_names: list[str], classes_def: dict, do_seed: bool) -> None:
    existing = {r["name"] for r in class_rows()}
    for name in seed_names:
        definition = next((c for c in classes_def["seeds"] if c["name"] == name), None)
        if definition is None:
            raise SystemExit(f"classe de depart '{name}' absente de corpus/classes.yaml")
        if name in existing:
            print(f"seed '{name}' : deja presente", flush=True)
            continue
        if not do_seed:
            raise SystemExit(f"classe de depart '{name}' absente ; relancer avec --seed pour la creer")
        command = [
            sys.executable, str(SITE_ROOT / "scripts" / "create_class.py"), name,
            str(REPO_ROOT / definition["semantic"]),
            "--structures", ",".join(definition["structures"]),
            "--format", definition.get("format", "xml"),
            "--language", definition.get("language", "en"),
        ]
        print(f"seed '{name}' : creation ({' '.join(definition['structures'])})", flush=True)
        subprocess.run(command, check=True, cwd=SITE_ROOT)


def login(base_url: str) -> requests.Session:
    email, password = os.environ.get("REPLAY_EMAIL"), os.environ.get("REPLAY_PASSWORD")
    if not email or not password:
        raise SystemExit("definir REPLAY_EMAIL et REPLAY_PASSWORD (compte creator/admin du site)")
    session = requests.Session()
    response = session.post(base_url + "/login", data={"email": email, "password": password}, allow_redirects=False)
    if response.status_code != 303:
        raise SystemExit(f"connexion refusee ({response.status_code})")
    return session


def reset_learned(session: requests.Session, base_url: str, steps: list[dict], assume_yes: bool) -> None:
    learned = [r for r in class_rows(include_merged=True) if r["status"] != SEED_STATUS]
    filenames = [Path(s["document"]).name for s in steps if "document" in s]
    db = SessionLocal()
    try:
        leftovers = list(db.scalars(select(Document).where(Document.filename.in_(filenames))))
        print("A supprimer (classes apprises) :", [f"{r['name']} ({r['docs']} doc.)" for r in learned] or "aucune")
        print("A supprimer (documents du scenario restants) :", sorted({d.filename for d in leftovers}) or "aucun")
        if not assume_yes:
            raise SystemExit("ajouter --yes pour confirmer la suppression")
        for row in learned:
            session.post(f"{base_url}/creator/classes/{row['id']}/delete", allow_redirects=False).raise_for_status()
        db.expire_all()
        for doc in db.scalars(select(Document).where(Document.filename.in_(filenames))):
            session.post(f"{base_url}/creator/documents/{doc.id}/delete", allow_redirects=False).raise_for_status()
    finally:
        db.close()
    print("etat apres nettoyage :")
    print_state()


def deposit(session: requests.Session, base_url: str, path: Path) -> int:
    content_type = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
    with open(path, "rb") as handle:
        response = session.post(
            base_url + "/creator/documents/new", files={"file": (path.name, handle, content_type)}, allow_redirects=False,
        )
    return response.status_code


def wait_and_collect(filename: str) -> dict:
    db = SessionLocal()
    started = time.time()
    try:
        while True:
            db.expire_all()
            doc = db.scalar(select(Document).where(Document.filename == filename).order_by(Document.created_at.desc()))
            if doc and str(doc.status).split(".")[-1] not in ("received", "processing"):
                break
            time.sleep(5)
        run = db.scalar(select(PipelineRun).where(PipelineRun.document_id == doc.id))
        for _ in range(40):  # les etapes "Cycle de vie" arrivent APRES la fin du run
            steps = list(db.scalars(select(PipelineStep).where(PipelineStep.run_id == run.id).order_by(PipelineStep.at)))
            if any(s.phase == "Cycle de vie" for s in steps) or str(doc.status).endswith("recognized"):
                time.sleep(4)
                break
            time.sleep(3)
        db.expire_all()
        doc = db.scalar(select(Document).where(Document.filename == filename).order_by(Document.created_at.desc()))
        steps = list(db.scalars(select(PipelineStep).where(PipelineStep.run_id == run.id).order_by(PipelineStep.at)))
        traced = [{"phase": s.phase, "label": s.label, "detail": s.detail} for s in steps if s.label.startswith(TRACED_LABELS)]
        return {"status": str(doc.status).split(".")[-1], "class": doc.class_name, "seconds": round(time.time() - started), "steps": traced}
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", help="fichier YAML de scenario (corpus/scenarios/*.yaml)")
    parser.add_argument("--seed", action="store_true", help="cree les classes de depart absentes (create_class.py)")
    parser.add_argument("--reset-learned", action="store_true", help="supprime les classes apprises et les documents du scenario avant de rejouer")
    parser.add_argument("--yes", action="store_true", help="confirme --reset-learned")
    parser.add_argument("--dry-run", action="store_true", help="affiche le plan sans rien ecrire")
    parser.add_argument("--report", help="ecrit un rapport JSON des resultats observes")
    args = parser.parse_args()

    scenario_path = Path(args.scenario).resolve()
    scenario = load_yaml(scenario_path)
    classes_def = load_yaml(CORPUS_ROOT / "classes.yaml")
    steps = scenario["steps"]
    base_url = os.environ.get("REPLAY_BASE_URL", "http://localhost:8010")

    print(f"scenario : {scenario['name']} - {scenario.get('description', '').strip()}")
    missing = [s["document"] for s in steps if "document" in s and not (CORPUS_ROOT / s["document"]).exists()]
    if missing:
        raise SystemExit(f"documents introuvables : {missing}")
    if args.dry_run:
        print("seeds :", scenario.get("seeds", []))
        for number, step in enumerate(steps, 1):
            print(f"  {number}. {step.get('document') or step.get('action')}  {step.get('note', '')}")
        print("etat actuel :")
        print_state()
        return

    session = login(base_url)
    ensure_seeds(scenario.get("seeds", []), classes_def, args.seed)
    if args.reset_learned:
        reset_learned(session, base_url, steps, args.yes)

    results = []
    for number, step in enumerate(steps, 1):
        if step.get("action") == "reevaluate":
            response = session.post(base_url + "/creator/corpus/reevaluate-unknown", allow_redirects=False)
            print(f"\n=== {number}. reevaluation des classes inconnues : HTTP {response.status_code}", flush=True)
            time.sleep(3)
            print_state()
            results.append({"action": "reevaluate", "http": response.status_code})
            continue
        path = CORPUS_ROOT / step["document"]
        code = deposit(session, base_url, path)
        print(f"\n=== {number}. {path.name} : depot HTTP {code}", flush=True)
        if code == 409:
            print("  deja present (meme sha256) : utiliser --reset-learned pour repartir d'un etat propre", flush=True)
            results.append({"document": step["document"], "http": code})
            continue
        outcome = wait_and_collect(path.name)
        print(f"  statut={outcome['status']} classe={outcome['class']} ({outcome['seconds']} s)", flush=True)
        for item in outcome["steps"]:
            print(f"  [{item['phase']}] {item['label']}: {item['detail']}", flush=True)
        print_state()
        results.append({"document": step["document"], "http": code, **outcome})

    if args.report:
        report = {"scenario": scenario["name"], "at": datetime.now(timezone.utc).isoformat(), "results": results, "classes": class_rows()}
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nrapport ecrit : {args.report}")


if __name__ == "__main__":
    main()
