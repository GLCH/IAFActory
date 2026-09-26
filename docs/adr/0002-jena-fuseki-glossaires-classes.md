# ADR 0002 : Jena et Fuseki, glossaires, classes documentaires

Statut : propose (2026-09-26).

## Jena et Fuseki : la même famille

Apache Jena est le projet : une bibliothèque Java pour RDF, SPARQL, les magasins (TDB2), les raisonneurs et SHACL. Fuseki est le serveur SPARQL du projet Jena, qui expose un magasin Jena en HTTP. Le README initial parlait de « Apache Jena » : dans le compose, le service se nomme `fuseki` parce que c'est le composant serveur de Jena, et il embarque le moteur Jena (TDB2). Aucune autre installation Jena n'est nécessaire côté serveur ; une bibliothèque Jena ne serait utile que pour du code Java embarqué, ce qui n'est pas prévu.

## Décision

1. Fuseki reste le service RDF : glossaires (SKOS), ontologies (OWL), schémas induits, en graphes nommés.
2. **Mise à jour 2026-09-26** : le glossaire métier est **commun à tous les projets** et structuré en **taxonomie de domaines** (SKOS `broader`/`narrower`). Une ontologie est rattachée à des domaines ; une classe documentaire utilise **un ou plusieurs domaines** et son ontologie est l'union de celles de ses domaines (descendants compris). Les termes induits d'un document non reconnu restent des termes candidats propres au projet ; leur promotion vers le glossaire commun est gouvernée (US3.10).
3. Un document peut appartenir à plusieurs classes documentaires (relation `IN_CLASS` avec score).
4. Une classe documentaire est de statut `provisoire`, `validee` ou `rejetee`.
5. Source de verite : Fuseki pour glossaires et ontologies ; Neo4j reçoit une copie de travail importée par n10s. La synchro va toujours de Fuseki vers Neo4j.

## Conséquences

- Les requêtes de raisonnement ou de validation (SHACL) passent par Jena/Fuseki ; le parcours de graphe et la recherche vectorielle par Neo4j.
- Recharger une ontologie crée une nouvelle version de graphe nommé ; les classes pointent vers une version précise.
- Coût : deux magasins de graphes à garder cohérents.

## Alternatives écartées

- Tout dans Neo4j (n10s seul) : simplifie l'infra mais perd SPARQL natif, le raisonnement et SHACL. À réévaluer si le besoin de raisonnement se révèle nul.
- Tout dans Fuseki : pas d'index vectoriel natif.

## Questions ouvertes

- Tranché : glossaire commun. Reste à définir : qui modifie la taxonomie commune, et qui promeut un terme candidat (US3.10, US5.6) ; classes et documents restent-ils propres au projet (hypothèse actuelle : oui) ?
- Le besoin de raisonnement OWL ou de validation SHACL est-il réel ? (Sinon, la valeur de Fuseki se réduit à SPARQL et au format d'échange.)
