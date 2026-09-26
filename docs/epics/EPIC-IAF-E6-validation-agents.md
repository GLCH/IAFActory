# EPIC IAF-E6 : validation des agents

Jira : IAF-6, stories IAF-34 a IAF-36 (US6.1 a US6.3). Statut : user stories redigees, forme des tests a confirmer.

Objectif : le creator dit ce que l'agent doit realiser et comment le verifier ; un agent n'est "valide" que s'il passe ses tests, dont ceux qu'il n'a jamais vus.

Questions transverses : forme d'un test (questions/reponses attendues, requetes graphe, juge LLM) ? seuil d'acceptation ? qui peut voir les resultats ?

## US6.1 Definir le livrable et ses tests
- En tant que creator, je decris le resultat attendu d'un agent et des tests qui le verifient.
- Prerequis : US4.1.
- Acceptance criteria : un test a une entree, un critere de reussite explicite et un seuil ; les tests sont versionnes avec l'agent ; un test sans critere est refuse.
- Exemples : entree "Qui dirige Acme ?", critere "la reponse cite Marie", seuil 1/1.
- Questions : criteres a juge LLM acceptes ? (a mesurer contre un jugement humain).

## US6.2 Executer la validation et voir le verdict
- En tant que creator, je lance les tests et vois un verdict par test et global.
- Prerequis : US6.1, US4.2.
- Acceptance criteria : resultat par test (reussi, echoue, erreur) avec la sortie de l'agent et sa provenance ; verdict global calcule par la regle de seuil ; historique conserve par version d'agent ; execution rejouable.
- Exemples : 9 tests sur 10 reussis avec un seuil de 90 pour cent : verdict "valide".

## US6.3 Validation hors echantillon
- En tant que creator, je reserve une partie des tests que l'agent (et son prompt) n'a pas servis a ajuster.
- Prerequis : US6.2.
- Acceptance criteria : les tests sont repartis en jeu de travail et jeu retenu ; le verdict "valide" exige la reussite du jeu retenu ; le jeu retenu n'est pas visible depuis la definition de l'agent ; l'ecart travail/retenu est affiche.
- Contexte : regle du projet : aucun benefice annonce sans validation hors echantillon.
- Exemples : 100 pour cent sur le jeu de travail mais 60 pour cent sur le retenu : verdict "non valide", ecart signale.
