# Lancer IAFActory en local

Ce document remplace la lecture eparpillee des README : tout ce qu'il faut
pour demarrer l'infra puis le site sur une machine de developpement. Verifie
reellement le 2026-09-27 (voir `poc/RESULTATS.md` pour les mesures et
incidents rencontres).

## 0. Prerequis

- Docker Desktop (le daemon doit tourner, pas seulement le client).
- Python 3.13.
- Un Ollama local avec au moins un modele de chat et un modele d'embedding
  (voir section 4). Si vous n'en avez pas : [ollama.com](https://ollama.com/download).
- `scripts/doctor.ps1` verifie le tout sans rien modifier.

### Conflits de port

Les valeurs par defaut de `.env.example` (5432, 7474, 7687, 3030, 4000, 11434)
supposent une machine sans autre projet Postgres/Neo4j/Fuseki/Ollama deja
lance. Si un port est deja pris (autre projet, autre conteneur), changez-le
dans `.env` (pas dans `.env.example` ni `compose.yaml`, qui restent la
reference machine-vierge) : `POSTGRES_PORT`, `NEO4J_HTTP_PORT`,
`NEO4J_BOLT_PORT`, `FUSEKI_PORT`, `LLM_GATEWAY_PORT`. Verifiez avant de lancer :

```powershell
Get-NetTCPConnection -LocalPort 5432,7474,7687,3030,4000 -ErrorAction SilentlyContinue
```

## 1. Secrets

```powershell
scripts/init-env.ps1
```

Genere `.env` (mots de passe, cles de la passerelle LLM) a partir de
`.env.example`. Ne l'ecrase jamais sans `-Force`. `.env` n'est jamais committe.

Completez ensuite a la main dans `.env` :

- `GEMINI_API_KEY` et `ANTHROPIC_API_KEY` si vous voulez ces fournisseurs
  (facultatif : la passerelle demarre sans, ces modeles echouent juste a
  l'appel). Sans aucune des deux, seul Ollama fonctionne.
- `OLLAMA_BASE_URL` : `http://ollama:11434` par defaut (conteneur du profil
  `llm`, jamais lance par defaut) ou `http://host.docker.internal:11434` pour
  reutiliser un Ollama deja installe sur la machine (recommande : evite de
  retelecharger des modeles).

## 2. Infra (Postgres, Neo4j, Fuseki, passerelle LLM, site)

```powershell
docker compose up -d --build postgres neo4j fuseki llm-gateway site
docker compose run --rm neo4j-init
```

Verifier que tout est sain :

```powershell
docker compose ps
```

Les 5 services doivent afficher `healthy` (jusqu'a ~1 min pour Neo4j au
premier demarrage : telechargement des plugins apoc et n10s). Le service
`site` applique ses migrations Alembic automatiquement a chaque demarrage
(idempotent) : rien a faire de plus.

### Verification manuelle (optionnelle)

```powershell
# Postgres, Neo4j, Fuseki, passerelle : ports lus depuis .env
curl http://127.0.0.1:3030/$/ping                     # Fuseki, anonyme -> 200
curl http://127.0.0.1:4000/health/readiness            # passerelle -> {"status":"healthy",...}
```

## 3. Ollama (modeles)

Si vous reutilisez l'Ollama de la machine (`OLLAMA_BASE_URL=http://host.docker.internal:11434`) :

```powershell
ollama pull nomic-embed-text     # embedding, 768 dimensions, ~274 Mo
ollama pull gemma3:4b            # chat, mesure fonctionnelle sur 8 Go de RAM
```

**`llama3.1:8b` echoue** sur une machine a RAM limitee : son contexte par
defaut (131072 tokens) fait tenter d'allouer 16 Gio de cache. `gemma3:4b`
avec `num_ctx: 4096` (deja configure dans `infra/llm-gateway/litellm.yaml`)
fonctionne. Si votre machine a plus de RAM/un GPU, `llama3.1:8b` redevient un
choix raisonnable : changer `infra/llm-gateway/litellm.yaml` (`ollama-local`)
et reconstruire `llm-gateway`.

Si vous preferez le conteneur Ollama dedie (profil `llm`, plus lent au
premier lancement car il retelecharge les modeles) :

```powershell
docker compose --profile llm up -d ollama
# puis les memes `ollama pull` en ciblant ce conteneur
```

## 4. Le site (`site/`)

Dockerise (ADR 0007) : demarre avec le reste de l'infra a l'etape 2, sur
`http://localhost:8010` (port par defaut, voir `SITE_PORT` dans `.env`).

### Premier compte admin

Aucune inscription libre (US5.1/US5.7) : le tout premier admin se cree hors
API, via le conteneur deja demarre :

```powershell
docker compose exec -it site python scripts/create_admin.py vous@exemple.fr
```

Ouvrez ensuite `http://localhost:8010/login`. Une fois connecte en admin,
les pages `/admin/users/new` (creer un creator ou un viewer) et
`/admin/users` (suivi et suppression des comptes) sont accessibles depuis le
tableau de bord.

### Developpement actif (rechargement a chaud, hors Docker)

Pour modifier le site sans reconstruire l'image a chaque changement :

```powershell
python -m venv .venv-site
.venv-site\Scripts\pip install -e .\site[dev]
cd site
..\.venv-site\Scripts\python.exe -m uvicorn app.main:app --reload --port 8011
```

Le conteneur `site` peut rester demarre en parallele (deux ports differents,
8010 et 8011) : les deux utilisent la meme base Postgres. `POSTGRES_HOST` et
`POSTGRES_PORT` par defaut (127.0.0.1 et la valeur de `.env`) conviennent
pour cet usage local ; ne pas les definir dans l'environnement de ce
terminal, sinon ils remplaceraient ce defaut.

## 5. Arreter

```powershell
docker compose stop        # garde les volumes (donnees conservees)
docker compose down -v     # efface tout (Postgres, Neo4j, Fuseki, passerelle)
```

Le site en rechargement a chaud (hors Docker, section 4) s'arrete avec
`Ctrl+C` dans son terminal.

## 6. Preuve de bout en bout (facultatif)

`poc/` contient un pipeline jetable (ingestion -> reconnaissance -> agent) qui
prouve que l'infra fonctionne ensemble, independant du site. Voir
`poc/README.md`. Resultats et limites mesures : `poc/RESULTATS.md`.
