# EPIC IAF-E4 : runtime d'agents et groupes

Jira : IAF-4, stories IAF-25 a IAF-28 (US4.1 a US4.4). Statut : user stories redigees, definition d'un agent a confirmer.

Objectif : un creator deploie des agents simples qui accomplissent une mission en s'appuyant sur le graph RAG, seuls ou en groupes.

Questions transverses : qu'est-ce qu'un agent (prompt + outils + ontologie + modele) ? fournisseur LLM (API Claude ou local) ? comment des agents d'un groupe communiquent ? isolation d'execution (un conteneur par agent ?) ? budget de jetons ?

## US4.1 Definir un agent
- En tant que creator, je definis un agent : mission, consignes, ontologie, outils autorises, modele.
- Prerequis : IAF-E5 US5.2 ; US3.3 pour l'ontologie.
- Acceptance criteria : agent enregistre en base avec version ; toute modification cree une nouvelle version ; l'agent ne peut referencer que des ressources de son projet ; une definition invalide est refusee avec le champ en cause.
- Exemples : agent "Analyste contrats" (mission, ontologie Contrat, outil graph-query) enregistre en v1 ; changer la consigne cree v2.
- Questions : format de definition (formulaire, YAML) ?

## US4.2 Lancer, suivre et arreter un agent
- En tant que creator, je lance un agent, suis son etat et son journal, et l'arrete.
- Prerequis : US4.1 ; fournisseur LLM.
- Acceptance criteria : etats explicites (en attente, en cours, termine, echec, arrete) ; journal des appels d'outils et des sorties consultable ; arret effectif en moins de N secondes (N fixe par mesure) ; plafond de duree et de jetons par execution.
- Exemples : un agent depasse son plafond de jetons : etat "echec" avec la cause "budget".
- Questions : execution synchrone ou file de taches ?

## US4.3 Faire tourner des agents en groupe
- En tant que creator, je regroupe des agents qui collaborent sur une mission commune.
- Prerequis : US4.2.
- Acceptance criteria : un groupe a des membres et un mode de coordination documente ; arreter le groupe arrete tous ses membres ; un membre en echec est visible sans masquer les autres ; les agents d'un groupe n'accedent qu'au projet du groupe.
- Exemples : groupe de 3 agents, l'admin arrete le groupe : les 3 passent en "arrete".
- Questions : modele de communication (messages, memoire partagee dans le graphe) ; nombre max d'agents.

## US4.4 Serveur MCP d'acces aux graphes
- En tant qu'agent, j'accede au graph RAG, aux ontologies et aux outils via MCP, limite a mon projet.
- Prerequis : US3.5.
- Acceptance criteria : outils exposes : recherche graph RAG, requete SPARQL en lecture, lecture d'ontologie ; ecriture interdite par defaut ; chaque appel est rattache a l'agent et au projet et journalise ; un appel hors projet est refuse.
- Contexte : version du protocole MCP et du SDK a verifier dans la doc avant choix.
- Exemples : un agent du projet A demande un noeud du projet B : refus journalise.
