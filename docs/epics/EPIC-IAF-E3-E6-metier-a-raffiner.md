# Epics metier (a raffiner avec l'utilisateur avant tout code)

Chaque epic passera au format Definition of Ready (description, prerequis, acceptance criteria, contexte, exemples) une fois le metier explique. Les questions ouvertes ci-dessous sont celles a poser.

Jira : IAF-3 a IAF-6 (E3 a E6).

## IAF-E3 Graph RAG et ontologies
- Perimetre : ingestion de documents, chunking, extraction d'entites/relations guidee par ontologie, graphe Neo4j, ontologies Fuseki, retrieval graphe + vecteur.
- Questions : formats de documents ? ontologies existantes (OWL, SKOS) ou a creer ? source de verite Neo4j ou Jena ? un graphe par projet creator ? volumes ?

## IAF-E4 Runtime d'agents et groupes
- Perimetre : deploiement d'un agent simple, execution en groupe, arret par l'admin, MCP pour l'acces aux graphes.
- Questions : qu'est-ce qu'un agent (prompt + outils + ontologie ?) ; comment les agents d'un groupe communiquent ; isolation des executions ; fournisseur LLM.

## IAF-E5 Site, roles et droits
- Perimetre : authentification, roles viewer/creator/admin, exclusions viewer posees par le creator (sur ses projets seulement), creation des creators par l'admin, arret/suppression par l'admin.
- Questions : authentification (locale, SSO) ; granularite des exclusions (projet, agent, document, graphe) ; un viewer peut-il etre restreint par defaut ?

## IAF-E6 Validation des agents
- Perimetre : le creator definit un livrable attendu et comment le valider (tests), execution automatique, resultat visible.
- Questions : forme d'un test (jeu de questions/reponses, requetes graphe, juge LLM) ; seuil d'acceptation ; validation hors echantillon obligatoire.
