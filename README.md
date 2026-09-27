# IAFActory

Fabrique d'agents IA simple : un graph RAG alimente les agents en documents (classes documentaires reconnues ou creees automatiquement, ontologies organisees en glossaires metiers), un site permet de creer et gerer des agents specialises, qui tournent seuls ou en groupes ; les viewers les utilisent en sessions. Beaucoup de graphes et d'ontologies (Neo4j pour le graphe de proprietes, Apache Jena Fuseki pour le RDF/SPARQL).

Etat : infra validee a froid (Postgres, Neo4j, Fuseki, passerelle LLM hybride), pipeline vertical de bout en bout prouve (`poc/`), site demarre (`site/`, connexion et roles). Pipelines CI/CD ecrits mais desactives. Suivi : projet Jira [IAF](https://gurvanleclech.atlassian.net/jira/core/projects/IAF/board), specification dans [docs/epics](docs/epics).

## Roles

| Role | Droits |
|---|---|
| viewer | Voit tout, sauf ce que le creator du projet lui interdit (uniquement sur les projets de ce creator). |
| creator | Cree un agent (ce qu'il doit realiser) et definit comment le valider (tests). Gere ses projets et leurs restrictions. |
| admin | Cree les creators. Peut arreter et effacer tout projet de tout creator. |

## Demarrage local (Windows)

Procedure complete, avec les conflits de port courants et le lancement du
site : [docs/runbooks/lancer-en-local.md](docs/runbooks/lancer-en-local.md).
En bref :

```powershell
scripts/init-env.ps1             # genere .env avec des mots de passe aleatoires
docker compose up -d --build postgres neo4j fuseki llm-gateway
docker compose run --rm neo4j-init
```

Services (127.0.0.1 uniquement) : Postgres 5432, Neo4j 7474 (navigateur) et 7687 (bolt), Fuseki 3030 (dataset `iaf`), passerelle LLM 4000 (LiteLLM + jev-router, Jev desactive, hybride Anthropic/Gemini/Ollama). LLM local optionnel : `--profile llm` (ou reutiliser un Ollama deja installe via `OLLAMA_BASE_URL`). Mode durci : `docker compose -f compose.yaml -f compose.secure.yaml up -d`.

Le site (`site/`, FastAPI) tourne en local hors Docker pendant le developpement : voir le runbook ci-dessus.

## Pipelines

Les workflows GitHub Actions sont ecrits mais **desactives** (declenchement manuel seulement). Voir [docs/runbooks/activer-les-pipelines.md](docs/runbooks/activer-les-pipelines.md).

## Documentation

- [Conception v2 : classification, agents specialises, sessions, connecteurs](docs/design/conception.md)
- [Architecture initiale](docs/architecture.md) (remplacee par la conception v2)
- [ADR 0001 : choix des magasins de donnees](docs/adr/0001-magasins-de-donnees.md)
- [ADR 0002 : Jena et Fuseki, glossaires, classes documentaires](docs/adr/0002-jena-fuseki-glossaires-classes.md)
- [ADR 0003 : connecteurs securises](docs/adr/0003-connecteurs-securises.md)
- [ADR 0004 : passerelle LLM et routage](docs/adr/0004-passerelle-llm.md)
- [ADR 0005 : ontologies structurelles et semantiques, metadonnees](docs/adr/0005-ontologies-structurelles-semantiques-metadonnees.md)
- [ADR 0006 : orchestration du pipeline documentaire](docs/adr/0006-orchestration-pipeline-documentaire.md)
- [ADR 0007 : pile applicative du site](docs/adr/0007-pile-site.md)
- [Ontologie structurelle de base](ontologies/structure/iaf-structure-base.ttl)
- [Lancer en local](docs/runbooks/lancer-en-local.md)
- [Preuve de bout en bout (poc/)](poc/RESULTATS.md)
- [Epics (Definition of Ready)](docs/epics)
