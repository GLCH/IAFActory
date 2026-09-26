# IAFActory

Fabrique d'agents IA simple : un graph RAG alimente les agents en documents (classes documentaires reconnues ou creees automatiquement, ontologies organisees en glossaires metiers), un site permet de creer et gerer des agents specialises, qui tournent seuls ou en groupes ; les viewers les utilisent en sessions. Beaucoup de graphes et d'ontologies (Neo4j pour le graphe de proprietes, Apache Jena Fuseki pour le RDF/SPARQL).

Etat : socle infra et pipelines (desactives). Le metier n'est pas commence. Suivi : projet Jira [IAF](https://gurvanleclech.atlassian.net/jira/core/projects/IAF/board), specification dans [docs/epics](docs/epics).

## Roles

| Role | Droits |
|---|---|
| viewer | Voit tout, sauf ce que le creator du projet lui interdit (uniquement sur les projets de ce creator). |
| creator | Cree un agent (ce qu'il doit realiser) et definit comment le valider (tests). Gere ses projets et leurs restrictions. |
| admin | Cree les creators. Peut arreter et effacer tout projet de tout creator. |

## Demarrage local (Windows)

```powershell
scripts/bootstrap.ps1            # simulation ; -Apply pour installer ce qui manque (winget)
scripts/init-env.ps1             # genere .env avec des mots de passe aleatoires
scripts/doctor.ps1               # verifie les prerequis, dont le daemon Docker
docker compose up -d --build
docker compose run --rm neo4j-init
```

Services (127.0.0.1 uniquement) : Postgres 5432, Neo4j 7474 (navigateur) et 7687 (bolt), Fuseki 3030 (dataset `iaf`). LLM local optionnel : `--profile llm`.

## Pipelines

Les workflows GitHub Actions sont ecrits mais **desactives** (declenchement manuel seulement). Voir [docs/runbooks/activer-les-pipelines.md](docs/runbooks/activer-les-pipelines.md).

## Documentation

- [Conception v2 : classification, agents specialises, sessions, connecteurs](docs/design/conception.md)
- [Architecture initiale](docs/architecture.md) (remplacee par la conception v2)
- [ADR 0001 : choix des magasins de donnees](docs/adr/0001-magasins-de-donnees.md)
- [ADR 0002 : Jena et Fuseki, glossaires, classes documentaires](docs/adr/0002-jena-fuseki-glossaires-classes.md)
- [ADR 0003 : connecteurs securises](docs/adr/0003-connecteurs-securises.md)
- [Epics (Definition of Ready)](docs/epics)
