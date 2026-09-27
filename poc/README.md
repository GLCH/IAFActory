# Preuve de bout en bout (PoC)

Scripts jetables qui font tourner un document reel a travers un chemin vertical
minimal : ingestion -> reconnaissance -> creation de classe -> structuration ->
question a un agent. Objectif : prouver que l'infra (Postgres, Neo4j, Fuseki,
passerelle LLM) fonctionne ensemble, avant d'ecrire l'application reelle
(IAF-E13, IAF-E3, IAF-E4).

## Ce que ce n'est PAS

- Pas l'implementation des epics : pas d'API, pas d'authentification, pas de
  role viewer/creator/admin, pas de file Postgres (ADR 0006), pas d'ecriture
  RDF dans Fuseki, pas de gouvernance du glossaire (US3.10).
- Analyse structurelle simplifiee : `python-docx` (titres, paragraphes,
  tableaux), pas Docling (US3.8 reste a evaluer separement : poids, licence,
  qualite sur de vrais documents).
- Reconnaissance simplifiee : un seul document, donc toujours "non reconnu" au
  premier passage ; la creation de classe (etape C) est simplifiee a un noeud
  DocumentClass avec un schema semantique brut (labels de types), pas une
  ontologie OWL versionnee dans Fuseki.
- Aucune mesure de precision/rappel hors echantillon (necessite un jeu annote,
  IAF-47) : ce PoC prouve la plomberie, pas la qualite de l'extraction.

## Prerequis

- Pile infra demarree : `docker compose up -d postgres neo4j fuseki llm-gateway`
  puis `docker compose run --rm neo4j-init`.
- `.env` du depot charge dans l'environnement (mots de passe, port Neo4j,
  cle maitre de la passerelle).
- Environnement Python : `python -m venv .venv-poc && .venv-poc/Scripts/pip
  install -r poc/requirements.txt` (Windows).

## Execution

```powershell
.\.venv-poc\Scripts\python.exe poc\sample_document.py
.\.venv-poc\Scripts\python.exe poc\ingest.py poc\out\fiche_technique_test.docx
.\.venv-poc\Scripts\python.exe poc\recognize_and_structure.py
.\.venv-poc\Scripts\python.exe poc\ask.py "Quelle est la resistance du materiau ?"
```

## Mesures reelles (a completer apres chaque execution)

Voir `poc/RESULTATS.md`, rempli avec les sorties reelles, pas des estimations.
