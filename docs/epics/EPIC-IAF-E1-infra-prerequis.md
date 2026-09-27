# EPIC IAF-E1 : infra locale et prerequis

Objectif : une machine Windows vierge arrive a `docker compose up` avec Postgres, Neo4j (apoc, n10s) et Fuseki sains, sans secret dans git.

Jira : IAF-1 (taches IAF-7 a IAF-13, T1 a T7 dans l'ordre).

Statut : taches 1 a 5 ecrites, 6 a faire (rien n'a encore ete demarre : daemon Docker arrete lors de la redaction).

## T1 Installer les prerequis (`scripts/bootstrap.ps1`)
- Description : installe git, gh, Docker Desktop, Node LTS, Python via winget, source `winget` uniquement.
- Prerequis : winget present.
- Acceptance criteria : simulation par defaut ; `-Apply` installe seulement ce qui manque ; aucun accord de licence de l'utilisateur n'est accepte a sa place hors `--accept-package-agreements` ; code de sortie non nul si un install echoue.
- Contexte : IDs verifies avec `winget show --exact --source winget` le 2026-09-26 (GitHub.cli, Docker.DockerDesktop, OpenJS.NodeJS.LTS, Git.Git, Python.Python.3.13). La source msstore demande un accord d'utilisation : evitee. Sur la machine de dev, `gh` est absent.
- Exemples : `bootstrap.ps1` sur machine sans gh affiche "gh manquant : installerait GitHub.cli" sans rien installer ; avec `-Apply`, installe gh puis affiche les etapes suivantes.
- Questions ouvertes : Terraform/Helm inutiles tant qu'il n'y a pas de cible de deploiement.

## T2 Generer les secrets (`scripts/init-env.ps1`)
- Description : cree `.env` depuis `.env.example` avec 3 mots de passe aleatoires.
- Prerequis : aucun.
- Acceptance criteria : n'ecrase pas un `.env` existant sans `-Force` ; mots de passe alphanumeriques de 32 caracteres (injectes tels quels dans shiro.ini) ; `.env` ignore par git.
- Contexte : `compose.yaml` refuse de demarrer si un mot de passe est vide (`${VAR:?}`).
- Exemples : deux executions consecutives : la seconde affiche "existe deja".

## T3 Diagnostic (`scripts/doctor.ps1`)
- Description : verifie outils, daemon Docker, `.env`, sans rien modifier.
- Acceptance criteria : sortie 1 si git, docker, node ou python manque, si le daemon est arrete ou si `.env` est absent ; `gh` et python optionnels non bloquants a terme (aujourd'hui `gh` signale comme manquant et bloquant).
- Contexte : etat mesure le 2026-09-26 : git 2.55, docker 29.1.3 (daemon arrete), node 24, python 3.14, gh absent.
- Questions ouvertes : `gh` requis ou optionnel ? Aujourd'hui requis (pousser, lancer les workflows).

## T4 Magasins de donnees (`compose.yaml`)
- Description : Postgres 17, Neo4j 5.26 Community avec apoc et n10s, job `neo4j-init` (contrainte d'URI n10s).
- Prerequis : T2.
- Acceptance criteria : `up -d --wait` sain ; ports lies a 127.0.0.1 ; volumes nommes ; `RETURN n10s.version()` repond ; rejouer `neo4j-init` ne produit aucune erreur.
- Contexte : ajouts 2026-09-26 : `infra/neo4j/init/02-model.cypher` (contraintes du modele de graphe) et `compose.secure.yaml` (magasins sans port publie, reseau `data` interne ; `docker compose config` valide, jamais demarre). `NEO4J_PLUGINS` et le tag 5.26-community verifies dans la doc et sur Docker Hub. Le telechargement des plugins se fait au premier demarrage (reseau requis).
- Exemples : mot de passe Neo4j absent de `.env` : `docker compose config` echoue avec "voir .env.example".
- Questions ouvertes : dimension et modele d'embedding pour l'index vectoriel (IAF-E3).

## T5 Image Fuseki (`infra/fuseki`)
- Description : image Jena Fuseki 6.2.0 sur Java 21, dataset TDB2 `iaf`, admin protege par mot de passe.
- Acceptance criteria : verification sha512 de l'archive ; `/$/ping` anonyme ; `/$/**` exige `admin` ; requete SPARQL `ASK {}` sur `/iaf/sparql` repond ; utilisateur non root.
- Contexte : pas d'image officielle ; URL `archive.apache.org/dist/jena/binaries/` non verifiee pour 6.2.0 (dlcdn confirme la version courante). Endpoints `update` et `data` du dataset non proteges (acceptable : port local).
- Questions ouvertes : proteger les endpoints d'ecriture des que l'API existe.

## T6 Validation a froid
- Description : premier `up` reel, mesures, corrections.
- Prerequis : Docker Desktop demarre ; T1 a T5.
- Acceptance criteria : les 3 services sains ; temps de demarrage et memoire par service notes dans "Contexte" ; tout ecart avec T4 et T5 corrige dans le code ET dans cet epic.
- **FAIT le 2026-09-27.** Postgres, Neo4j (apoc+n10s) et Fuseki sains au premier `docker compose up`. Fuseki : `/$/ping` anonyme 200, `/$/server` sans auth 401, avec auth 200, `ASK {}` sur `/iaf/sparql` 200 (4.57 s a froid, 119 ms ensuite). `shiro.ini` genere fonctionne tel qu'ecrit, aucune correction necessaire. Neo4j sain en ~50 s (telechargement des plugins inclus). `neo4j-init` (contraintes) applique sans erreur.
- **Ecart reel corrige** : conflits de port sur la machine de dev (un autre projet local occupe 5432, 7474, 7687, 3030) ; decales dans `.env` (machine-specifique, pas dans les defauts de `compose.yaml`/`.env.example`). Detail et reproduction : `poc/RESULTATS.md`.
- Exemples : si le build Fuseki echoue sur le sha512, corriger l'URL ou le format du fichier de somme (non rencontre : le sha512 a passe du premier coup).

## T8 Passerelle LLM (infra)
- Voir IAF-E11 US11.1 (IAF-68) : `infra/llm-gateway`, base `litellm`, service `llm-gateway`.
- **FAIT le 2026-09-27** : construite et demarree avec succes (LiteLLM v1.98.0 + jev-router, migrations Prisma appliquees, hook charge sans erreur). Detail : `poc/RESULTATS.md` et EPIC-IAF-E11 US11.1.

## T7 LLM local optionnel
- Description : profil `llm` Ollama ; choix du modele d'embedding et du modele de generation.
- Acceptance criteria : `--profile llm up` demarre Ollama ; un modele d'embedding tire et interrogeable.
- **Partiellement fait le 2026-09-27**, mais avec un Ollama natif de l'hote plutot que le conteneur du profil `llm` (deja present sur la machine de dev, evite un telechargement redondant ; `OLLAMA_BASE_URL` rend les deux interchangeables). Modele d'embedding retenu et mesure : `nomic-embed-text` (768 dimensions). Modele de generation : `llama3.1:8b` **echoue** sur cette machine (memoire insuffisante pour son contexte par defaut de 131072 tokens, tentative d'allocation de 16 Gio) ; `gemma3:4b` avec `num_ctx: 4096` fonctionne. Detail : `poc/RESULTATS.md`.
- Questions ouvertes : le conteneur du profil `llm` (image `ollama/ollama:latest`, non figee) reste a tester independamment de l'Ollama de l'hote ; GPU disponible sur la machine cible de production ?
