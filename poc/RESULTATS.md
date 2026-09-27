# Resultats mesures - preuve de bout en bout (2026-09-27)

Machine de dev : 8 coeurs, ~8 Go RAM visibles par Docker Desktop, CPU seul
(pas de GPU). Ollama natif de l'hote (modeles deja presents : llama3.1:8b,
gemma3:4b, qwen2.5-coder:1.5b-base), reutilise par la passerelle via
`OLLAMA_BASE_URL=http://host.docker.internal:11434`.

## 1. Infra (validation a froid, IAF-1 T6)

| Service | Resultat | Detail mesure |
|---|---|---|
| Postgres 17 | Sain | `docker compose ps` healthy des le premier demarrage |
| Neo4j 5.26 + apoc + n10s | Sain | plugins telecharges et installes au premier demarrage ; healthy en ~50 s |
| Fuseki 6.2.0 | Sain | `/$/ping` anonyme 200 ; `/$/server` sans auth 401 ; avec auth 200 ; `ASK {}` sur `/iaf/sparql` : premiere requete 200 OK en 4.57 s (ouverture du dataset), requetes suivantes 119 ms |
| Passerelle LLM (LiteLLM v1.98.0 + jev-router) | Saine | `/health/readiness` -> `{"status":"healthy","db":"connected"}` ; migrations Prisma appliquees sans erreur ; hook jev-router charge sans erreur ; 12 modeles au catalogue (`/v1/models`) |

**Corrections reelles apportees suite a cette validation** (pas des hypotheses : mesurees) :

- **Conflits de port** sur cette machine de dev : un autre projet occupe deja 5432 (Postgres), 7474/7687 (Neo4j) et 3030 (un service nomme "langfuse"). Decales dans `.env` local vers 5433/7475/7688/3031. `compose.yaml` et `.env.example` restent inchanges (les valeurs par defaut sont correctes pour une machine vierge).
- **Ollama** : `llama3.1:8b` echoue systematiquement avec le message `llama-server reported out-of-memory ... failed to allocate buffer of size 17179869184` (16 Gio) car son `context_length` par defaut (131072) fait allouer un cache KV enorme. Corrige en passant a `gemma3:4b` avec `num_ctx: 4096` explicite (`extra_body.options.num_ctx` dans `litellm.yaml`, syntaxe verifiee). A chaud, gemma3:4b repond en 1-2 s ; a froid (modele decharge par Ollama apres inactivite), le chargement seul prend 25-45 s.
- **Embedding** : `nomic-embed-text` (274 Mo, tire sur l'Ollama de l'hote) fonctionne du premier coup via `model: ollama/nomic-embed-text` (prefixe different du chat : `ollama/`, pas `ollama_chat/`). Dimension reelle observee : **768**.

**Non teste dans cette passe** : profil `--profile llm` (conteneur Ollama dedie, puisqu'on a reutilise celui de l'hote) ; mode durci `compose.secure.yaml` ; alias `iaf-extraction`/`iaf-agent`/`iaf-cadrage`/`iaf-judge`/`iaf-auto` (tous pointent vers Anthropic, sans cle : non appelables tant que `ANTHROPIC_API_KEY` ou `GEMINI_API_KEY` n'est pas renseignee).

## 2. Pipeline vertical minimal (`poc/`)

Document synthetique `.docx` (fiche technique inventee pour ce test, 7 elements structurels : 4 sections, 2 paragraphes, 1 tableau de 6 lignes).

| Etape | Resultat | Duree mesuree |
|---|---|---|
| I - ingestion (`ingest.py`) | 7 elements structurels, 3 chunks, embeddings 768-dim, index vectoriel Neo4j cree | 26.7 s (3 appels d'embedding inclus) |
| R/C - reconnaissance + creation de classe (`recognize_and_structure.py`, 1er essai) | **Bloque** : un appel LLM est reste en attente durablement (tue apres ~8 min sans reponse) | echec, cause exacte non isolee |
| idem, apres ajout d'un `max_tokens=500` sur les appels de chat | Classe provisoire creee, 3 chunks structures, 10 liens entite-chunk, 2 attributs, 0 relation | ok |
| S - agent (`ask.py`) | Recherche vectorielle : 3 chunks retrouves, scores 0.888 / 0.874 / 0.843 (le tableau en tete, logique) ; reponse correcte et sourcee | 150.4 s (recharge a froid du modele) |

**Reponse reelle obtenue** a la question *« Quelle est la resistance a la traction du joint et de quel materiau est-il fait ? »* :

> La resistance a la traction du joint est de 9 MPa (section "Tableau (7x2)"). Le materiau de ce joint est de l'EPDM (section "Tableau (7x2)" et section "Ce joint torique...").

Correcte et sourcee sur les deux points. Prouve que la chaine ingestion -> embeddings -> Neo4j -> recherche vectorielle -> LLM -> reponse citee fonctionne reellement de bout en bout, pas seulement sur le papier.

## 3. Qualite de l'extraction (etape S) : limites reelles, pas des hypotheses

L'extraction par `gemma3:4b` sans calibration est mediocre sur le tableau : chaque cellule de valeur ("45 mm", "3 mm", "9 MPa"...) devient sa propre Entity au lieu d'un attribut sur l'entite "EPDM", alors que le paragraphe de resume donne bien 2 attributs corrects (`Temperature Min`/`Max`) sur "EPDM". Aucune relation n'a ete extraite (0). C'est la preuve concrete, avec un vrai exemple, de la regle du projet : **aucun seuil ni aucune qualite d'extraction ne doit etre suppose sans mesure hors echantillon (IAF-47)**. Ce PoC n'a pas de jeu annote et ne pretend a aucune precision/rappel.

## 4. Incident non explique

Le premier essai de `recognize_and_structure.py` s'est bloque sur un appel de chat (probablement le chunk du tableau) sans jamais repondre, alors qu'un appel manuel equivalent, juste apres, a repondu en 47 s. Cause non identifiee (contention memoire avec d'autres processus de la machine ? etat interne d'Ollama ?). Mitige par un `max_tokens` explicite plutot que resolu : a surveiller si le phenomene se reproduit (IAF-91, lots et pauses).

## 5. Consequences pour les epics (a reporter, fait le 2026-09-27)

- IAF-1 T6 (validation a froid) : DONE, ports corriges dans `.env` local.
- IAF-68/US11.1 (deploiement passerelle) : DONE, healthcheck confirme reellement, pas seulement en theorie.
- IAF-71/US11.4 (jev-router) : le repli sur `claude-sonnet` n'a pas ete teste (pas de cle Anthropic) ; le chemin Ollama seul est valide.
- IAF-79/US3.8 (analyse structurelle) : ce PoC utilise `python-docx`, pas Docling ; Docling reste a evaluer separement (poids, licence, qualite), ce PoC ne tranche pas cette question.
- IAF-86..87/US13.3-13.4 (R et S) : la mecanique fonctionne ; la qualite ne prouve rien sans jeu annote (IAF-47), et `gemma3:4b` sans calibration produit une extraction mediocre sur les tableaux.
