# EPIC IAF-E19 : services d'ingestion, de structuration et d'exposition agentisés

Statut : rédigé le 2026-10-03, demande explicite de l'utilisateur. Décision d'architecture : [ADR 0009](../adr/0009-services-agentises.md) (**acceptée le 2026-10-03** : pipelines déterministes, MCP, trois conteneurs séparés). Liens : [E13](EPIC-IAF-E13-pipeline-documentaire.md) (pipeline), [E18](EPIC-IAF-E18-sources-externes-et-scans.md) (connecteurs, OCR), [E4](EPIC-IAF-E4-runtime-agents.md) (agents).

Objectif : l'ingestion, la structuration et l'exposition sont des services (agents) qui détiennent seuls l'accès à Neo4j, Jena et Postgres ; le site devient un client (authentification, rôles, interface). Les connecteurs alimentent le service d'ingestion ; leurs contenus sont accessibles aux viewers et aux creators (« editors ») par une page commune.

« Editor » = rôle `creator` (aucun nouveau rôle).

## US19.1 Page commune d'accès aux connaissances (FAIT, 2026-10-03)
- En tant que viewer ou creator, je parcours ce que le service sait et je pose une question dont la réponse est le savoir extrait du système.
- Acceptance criteria : pages `/knowledge`, `/knowledge/classes/{id}` et `/ask` ; viewer limité aux classes officielles, creator voit aussi les provisoires et un lien « Éditer la classe » ; admin et anonyme refusés sur `/knowledge` ; connecteurs **approuvés** visibles de tous (nom, type, dernière synchronisation, sans configuration ni propriétaire) ; **la réponse vient du service d'exposition (MCP)**, jamais d'une requête du site.
- **Savoir extrait, expliqué, ancré** : voie graphe sémantique et/ou RAG selon les données ; réponse et explication uniquement à partir des données enregistrées, chaque phrase citant [n] ; **aveu d'ignorance** quand il n'y a rien (`inconnu`), qu'un terme sans détail (`terme_sans_detail`), ou que les données voisines ne répondent pas (`donnees_insuffisantes`) ; aucune réponse n'est fabriquée dans ces cas et, hors `donnees_insuffisantes`, le modèle n'est pas appelé.
- Mesures : 29 tests du service (Neo4j réel, modèle simulé, dont un test MCP de bout en bout), 11 tests de pages du site (faux client), 5 tests du client MCP contre le vrai service.
- Limites : pas de pagination ; la provenance des documents n'est pas stockée (question 5 de l'ADR, non tranchée).

## US19.2 Lectures restantes derrière la couche d'exposition
- En tant que mainteneur, je veux que `ask`, les vues de classes, le corpus et les taxonomies lisent par `app/services/exposition`.
- Prérequis : US19.1. Acceptance criteria : plus aucune requête Cypher dans ces routes ; mêmes pages, mêmes résultats (comparaison avant et après sur les classes de test) ; `documents.py`, `ask.py` et `admin.py` sortent de `LEGACY_STORE_ACCESS`.
- Mesure de départ : `documents.py` 24 usages de `get_driver`, 47 requêtes Cypher, 7 appels `ontology` ; `ask.py` 2 ; `admin.py` 2 et 2.
- Statut : non fait.

## US19.3 Règle d'architecture testée (FAIT, 2026-10-03)
- Acceptance criteria : `tests/test_architecture.py` échoue si un routeur hors liste héritée ouvre `get_driver` ou `ontology` ; échoue aussi si un routeur migré reste dans la liste (cliquet) ; la page des connaissances ne contient aucune requête.

## US19.4 Contrats d'agent (partiel : exposition, 2026-10-03)
- Chaque service déclare sa mission, ses outils (fonctions typées pydantic, schémas publiés par MCP) et le contexte d'appelant (rôle) qu'il exige. Fait pour l'exposition (4 outils, modèles `Answer`, `ClassSummary`, `ClassDetail`). À faire pour l'ingestion et la structuration. Décisions de l'ADR : pipelines déterministes, MCP.

## US19.5 Exposition en processus séparé (FAIT pour l'exposition, 2026-10-03)
- Le service d'exposition tourne dans son conteneur (`exposition/`, MCP Streamable HTTP, jeton Bearer, rôle `viewer` ou `creator`) ; le site l'appelle par un client MCP. Vérifié : refus sans jeton et avec un faux jeton (401), refus d'un rôle inconnu, résultats typés. Reste : le site garde d'autres accès directs à Neo4j (`documents.py`, `admin.py`, voir US19.2) ; la segmentation réseau complète est US19.8.

## US19.6 Service d'ingestion et point d'entrée des connecteurs
- Les connecteurs approuvés poussent leurs fichiers au service d'ingestion avec leur provenance ; les documents portent `source`, `connecteur`, `date`. Acceptance criteria : un document issu d'un connecteur affiche sa source sur la page des connaissances ; OpenRiC reste en pause tant que « comment et quand » n'est pas défini. Statut : non fait.

## US19.7 Service de structuration
- Reconnaissance, création et cycle de vie des classes, induction d'ontologie, fusion, promotion, réduction, taxonomies, derrière un contrat de service. Statut : non fait.

## US19.8 Segmentation réseau
- Le conteneur du site n'a plus d'identifiants Neo4j, Jena ni des tables du pipeline ; seuls les services y accèdent (`compose.secure.yaml`). Statut : non fait.

## Décisions de l'utilisateur
Prises le 2026-10-03 (voir l'ADR 0009) : pipelines déterministes, MCP, trois conteneurs séparés, recherche qui retourne le savoir extrait avec aveu d'ignorance. Reste à trancher : la provenance montrée aux utilisateurs (quand un connecteur sera rouvert).
