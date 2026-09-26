# ADR 0001 : magasins de donnees

Statut : propose (2026-09-26). A confirmer avant l'epic IAF-E3.

## Decision

| Besoin | Choix | Raison |
|---|---|---|
| Donnees applicatives (utilisateurs, roles, agents, groupes, droits viewer) | PostgreSQL 17 | Relationnel, transactions, contraintes pour les regles de roles. |
| Graphe de connaissances (graph RAG, entites, relations, chunks, index vectoriel) | Neo4j 5.26 Community + plugins `apoc` et `n10s` | Graphe de proprietes, index vectoriel natif Neo4j 5, n10s pour passer du RDF au graphe de proprietes. |
| Ontologies (RDF/OWL, SPARQL, raisonnement) | Apache Jena Fuseki 6.2.0 (TDB2) | Source de verite des ontologies ; SPARQL standard. Requiert Java 21. |
| LLM et embeddings locaux | Ollama, profil optionnel | Permet de travailler sans cle API ; le fournisseur reste a choisir. |

Pas de stockage objet au demarrage : MinIO n'est pas retenu (distribution d'images Docker communautaires non garantie). Les documents sources sont conserves sur volume par l'API, avec une abstraction de stockage pour changer plus tard.

## Points verifies

- `NEO4J_PLUGINS` accepte `apoc`, `apoc-extended`, `bloom`, `genai`, `graph-data-science`, `n10s` (doc operations Neo4j 5).
- Le tag `neo4j:5.26-community` existe sur Docker Hub.
- Fuseki n'a pas d'image officielle sur Docker Hub : image construite depuis la release Apache avec verification sha512.

## Non verifie (a mesurer au premier `up`)

Le daemon Docker etait arrete lors de la redaction : le build de l'image Fuseki, la syntaxe de `shiro.ini` genere et le healthcheck Neo4j n'ont pas ete executes. Voir IAF-E1 tache 6.

## Questions ouvertes

- Deux magasins de graphes (Neo4j et Jena) : quelle est la source de verite d'une ontologie, et dans quel sens se synchronise-t-on ?
- Neo4j Community n'a pas de controle d'acces fin par graphe ni de multi-bases : l'isolation par projet creator se fera dans l'API.
