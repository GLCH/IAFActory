# ADR 0009 : services d'ingestion, de structuration et d'exposition agentisés

Statut : **accepté le 2026-10-03** (réponses de l'utilisateur aux questions ouvertes, voir « Décisions de l'utilisateur » en fin de document). Implémenté : le service d'exposition (conteneur séparé, serveur MCP), la page des connaissances et la page de questions qui l'appellent, le test d'architecture. Ingestion et structuration restent à extraire. Epic : [EPIC-IAF-E19](../epics/EPIC-IAF-E19-services-agentises.md).

## Contexte et état mesuré

Demande de l'utilisateur : « le site accède aux différents supports Apache Jena, Postgres et Neo4j ; les services d'ingestion, de structuration et d'exposition doivent être agentisés, pas des fonctionnalités du site uniquement ; des connecteurs alimenteront le service et leurs contenus seront accessibles aux viewers et aux editors ».

État mesuré le 2026-10-03 (`site/tests/test_architecture.py`) : le site est un monolithe FastAPI qui ouvre lui-même les trois magasins. `routers/documents.py` (1 161 lignes) contient 24 usages de `get_driver`, 47 requêtes Cypher et 7 appels aux ontologies ; `ask.py` et `admin.py` en contiennent aussi. Le pipeline (`pipeline.py`, `worker.py`, `class_lifecycle.py`...) tourne dans le même processus (un `ThreadPoolExecutor`, ADR 0006 option A). Conséquences : le conteneur du site détient les identifiants des trois magasins ; l'ingestion ne peut pas être appelée sans le site ; l'accès aux données n'est pas réutilisable par un agent ; la règle d'accès par rôle est répartie dans les routes.

« Editor » dans la demande est lu comme le rôle `creator` (pas de nouveau rôle créé).

## Découpage proposé

Les quatre phases du pipeline documentaire (I, R, C, S, conception section 13) se regroupent en trois services :

| Service | Mission | Reprend |
|---|---|---|
| **Ingestion** | recevoir un contenu (dépôt, connecteur), l'analyser (structure, OCR), le découper, l'embedder | phase I, `ocr_struct`, formats, connecteurs (`app/connectors`), garde réseau |
| **Structuration** | rattacher à une classe ou en créer, induire l'ontologie, fusionner, promouvoir, réduire, taxonomies | phases R, C, S, `class_lifecycle`, `class_merge`, `class_reduction`, `taxonomy_builder`, Jena |
| **Exposition** | donner accès aux connaissances et répondre | `app/services/knowledge.py`, `ask`, graph RAG |

## Décisions proposées

1. **Seuls les services parlent aux magasins** (Neo4j, Jena, tables du pipeline). Le site garde l'authentification, les rôles, l'interface et ses propres tables (utilisateurs, paramètres). Il appelle les services.
2. **Un service agentisé = une mission, un jeu d'outils typés, un contrat d'appel.** Les outils sont les fonctions déjà isolées (`app/services/knowledge.py` en est la première : sans état, entrées et sorties sérialisables, lecture seule). Aucune décision n'est prise sur le degré d'autonomie LLM (voir questions).
3. **Contrat d'appel : HTTP et JSON (OpenAPI) d'abord**, adaptateur MCP ensuite (la conception prévoit MCP pour les outils des agents). Authentification de service à service par jeton, plus un **contexte d'appelant** signé (identifiant, rôle) que le service re-vérifie : Neo4j Community n'a pas de contrôle d'accès fin (ADR 0001), la règle de visibilité doit donc vivre dans la couche service (déjà le cas pour `scope` : un viewer ne voit que les classes officielles).
4. **Les connecteurs alimentent le service d'ingestion** par un point d'entrée unique (`intake`), pas par le site : un connecteur approuvé (ADR 0003, E9, E18) pousse des fichiers avec leur provenance. La provenance (source, connecteur, date) est stockée sur le document pour que viewers et creators sachent d'où vient une connaissance.
5. **Asynchrone** : l'ingestion et la structuration restent pilotées par la file Postgres (ADR 0006, option A) ; l'exposition est synchrone.
6. **Migration par étranglement, un cran à la fois, avec un cliquet testé** : une route qui ouvre un magasin est tolérée seulement si elle est dans la liste héritée de `test_architecture.py`, qui ne peut que rétrécir.

## Étapes

| Étape | Contenu | État |
|---|---|---|
| 0 | `app/services/knowledge.py`, page commune viewer et creator, test d'architecture | fait le 2026-10-03 |
| 1 | migrer les lectures restantes (ask, vues de classes, corpus, taxonomies) derrière `app/services/exposition` | à faire |
| 2 | contrats typés (pydantic) et contexte d'appelant pour chaque service | à faire |
| 3 | exposition extraite en processus séparé (conteneur, réseau interne) | à faire |
| 4 | ingestion extraite avec point d'entrée `intake` pour les connecteurs, provenance | à faire |
| 5 | structuration extraite ; le site n'a plus aucun identifiant de magasin (segmentation réseau, `compose.secure.yaml`) | à faire |

## Risques et contreparties

- Plus de pièces à héberger et à superviser ; latence ajoutée sur chaque lecture.
- Les écritures multi-magasins (Neo4j, Jena, Postgres) ne sont déjà pas transactionnelles ; les séparer entre services ne l'empire pas mais n'y remédie pas non plus.
- Les modèles (classe, concept, document) seront partagés entre site et services : un paquet de schémas communs ou une duplication assumée sont à trancher.
- Un « agent » dont le plan est écrit en code est un service ordinaire : le gain est la séparation et la réutilisation des outils, pas l'autonomie.

## Décisions de l'utilisateur (2026-10-03)

1. **Autonomie : pipelines déterministes.** L'ordre des opérations est écrit en code, pas planifié par un modèle. Le modèle n'intervient que pour reformuler et expliquer, dans le service d'exposition, des données déjà retenues par une récupération déterministe.
2. **Protocole : MCP** (SDK `mcp` 2.3.0, Streamable HTTP, jeton Bearer).
3. **Déploiement : trois conteneurs séparés**, architecture modulaire. Un conteneur par service ; seul `exposition` existe aujourd'hui.
4. **Contenu retourné par une recherche** : le savoir extrait du système, soit par RAG (passages de documents), soit par le graphe sémantique (concepts, définitions, hiérarchie, attributs et relations d'entités) quand les données y sont. Le service **enrichit et explique, mais uniquement à partir des données enregistrées**. S'il n'y a rien, ou seulement un terme sans détail, **il indique son ignorance**.
5. **Provenance** : question non comprise par l'utilisateur, reformulée ci-dessous ; non tranchée.

### Provenance, en clair
Quand un connecteur apportera un document, le système saura d'où il vient : quel connecteur, quand. La question est seulement : faut-il **montrer** cette origine aux utilisateurs (par exemple « source : archives X, synchronisé le 12/10 » à côté d'une réponse) ou la garder en interne ? Aujourd'hui aucun connecteur n'est actif, donc rien à montrer. À trancher quand le premier connecteur sera rouvert.

## Réalisation du service d'exposition (2026-10-03)

- **Conteneur** `exposition` (`exposition/`, image Python 3.13, utilisateur non root) : lit Neo4j et la passerelle LLM, **aucun accès à Postgres ni à Fuseki**, aucun port publié, joignable seulement du site avec `EXPOSITION_TOKEN` (le service refuse de démarrer sans jeton).
- **Outils MCP typés** : `ask_knowledge` (réponse ancrée), `search_knowledge` (correspondances sans modèle), `list_classes`, `get_class`. Le site transmet `caller_role` (`viewer` ou `creator`, l'admin est traité comme viewer) ; un autre rôle est refusé. Le rôle n'est pas prouvé cryptographiquement : le jeton prouve que l'appelant est le site, qui reste responsable de ne transmettre que le rôle réel de l'utilisateur (limite acceptée).
- **Récupération déterministe** : concepts (libellé ou définition), hiérarchie, entités avec attributs et relations, passages de documents par similarité vectorielle puis **filtre lexical**. Mesure du 2026-10-03 : le score vectoriel ne discrimine pas la pertinence (une recette de tarte obtient 0,835 contre 0,84 à 0,90 pour des questions pertinentes), donc un plancher sur ce score ne peut pas porter l'aveu d'ignorance ; un passage n'est retenu que s'il contient assez de termes de la question.
- **Ancrage mécanique** : chaque phrase de la réponse doit citer une donnée numérotée [n] ; les phrases sans citation valide sont retirées ; une réponse sans citation valide est écartée au profit des données brutes (tracé dans `notes`).
- **Statuts** : `repondu`, `donnees_insuffisantes` (le modèle déclare que les données ne répondent pas : aucune réponse fabriquée), `terme_sans_detail`, `inconnu` et `requete_trop_courte` (ces trois-là sans appel au modèle).
- **Limites** : l'ancrage est lexical, donc un synonyme absent des données n'est pas retrouvé ; le modèle peut citer à tort une donnée existante (le contrôle vérifie l'existence des citations, pas leur pertinence) ; la voie graphe dépend de l'extraction d'entités et de relations déjà faite à l'ingestion.
