# Architecture cible (version initiale)

Remplacee par [conception.md](design/conception.md) (2026-09-26). Conservee pour l'historique.

Ce qui existe : les magasins de donnees (compose). Le reste est la cible, a raffiner par epic.

```
Site (creator / viewer / admin)
        |
      API  ---- Postgres      utilisateurs, roles, agents, groupes, droits viewer
        |
        +------ Neo4j         graph RAG : documents, chunks, entites, index vectoriel
        +------ Fuseki        ontologies RDF/OWL, SPARQL
        |
  Runtime d'agents (groupes) ---- MCP (outils, acces graphes) ---- LLM (API ou Ollama)
```

## Roles et regles d'acces (a formaliser dans IAF-E5)

- Un projet appartient a un creator.
- viewer : lecture de tout, moins les exclusions posees par le creator proprietaire, uniquement sur ses projets.
- creator : cree et gere ses agents et leurs tests de validation.
- admin : cree les creators ; arret et suppression de tout projet.
- Les regles sont appliquees dans l'API (Neo4j Community n'offre pas de controle fin par graphe).

## Flux

1. Ingestion : document, decoupage en chunks, extraction d'entites et relations guidee par une ontologie, ecriture dans Neo4j.
2. Ontologies : gerees dans Fuseki (graphes nommes), importees vers Neo4j via n10s.
3. Agent : recoit une mission, interroge le graph RAG via MCP, produit un resultat, valide par les tests definis par le creator.
4. Groupes : plusieurs agents tournent ensemble ; l'admin peut tout arreter.
