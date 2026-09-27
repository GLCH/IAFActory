#!/bin/sh
# Applique les migrations puis demarre le serveur. Idempotent : "alembic
# upgrade head" ne rejoue rien si la base est deja a jour (verifie US11.1-style
# au premier demarrage reel de ce service).
set -eu

alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
