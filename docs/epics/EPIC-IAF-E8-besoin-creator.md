# EPIC IAF-E8 : comprendre le besoin du creator

Jira : IAF-38, stories IAF-48 a IAF-51 (US8.1 a US8.4). Statut : rédigé le 2026-09-26. Conception : [conception.md](../design/conception.md) section 6.

Objectif : le creator exprime un besoin ; le service le comprend, dit comment y accéder et comment le réaliser, et prépare l'agent (spécialité, sources, tests). Le creator valide chaque étape.

Questions transverses : le creator est-il technique ? quel niveau de guidage ? un besoin peut-il donner plusieurs agents ?

## US8.1 Exprimer un besoin
- En tant que creator, je décris en langage naturel ce que je veux obtenir.
- Prérequis : IAF-30 (connexion), IAF-31 (projet).
- Acceptance criteria : le brief est enregistré et versionné dans le projet ; champs libres plus trois champs guidés : qui utilise le résultat, quelle décision il éclaire, exemples de bonnes réponses ; un brief vide est refusé.
- Exemples : « Je veux répondre aux questions des commerciaux sur les clauses de nos contrats » est enregistré avec utilisateurs cibles « commerciaux ».

## US8.2 Cadrer avec un assistant
- En tant que creator, je veux que l'assistant reformule mon besoin et me pose les questions manquantes avant de proposer quoi que ce soit.
- Prérequis : US8.1, fournisseur LLM.
- Acceptance criteria : reformulation soumise à validation explicite ; au plus N questions par tour (N fixé par mesure de fatigue) ; la conversation de cadrage est conservée ; l'assistant n'agit sur aucune ressource sans validation.
- Exemples : à « répondre sur nos contrats », l'assistant demande : quels types de contrats, quelles langues, faut-il citer la page ? Le creator répond, la reformulation est corrigée puis validée.
- Questions : cadrage en une session ou reprenable sur plusieurs jours ?

## US8.3 Proposer un agent prêt à valider
- En tant que creator, je reçois une proposition complète : spécialité, sources et accès, tests de validation, approche.
- Prérequis : US8.2, IAF-E7 (classes), IAF-25.
- Acceptance criteria : proposition = spécialité (classes existantes ou à créer), sources et connecteurs nécessaires, tests candidats au format US6.1 avec un jeu retenu, description de l'approche en langage clair ; chaque élément est éditable ; rien n'est créé avant validation ; la proposition cite les classes et documents qui la justifient.
- Exemples : besoin « clauses de contrats » : spécialité {Contrat}, source {dépôt de fichiers}, 10 tests dont 3 retenus.

## US8.4 Signaler les écarts
- En tant que creator, je veux savoir ce qui manque pour que l'agent fonctionne.
- Prérequis : US8.3.
- Acceptance criteria : liste d'écarts typés : classe absente ou vide, source inaccessible, glossaire manquant, tests insuffisants ; chaque écart propose une action (déposer des documents, configurer un connecteur, charger un glossaire) ; l'agent reste en brouillon tant qu'un écart bloquant existe.
- Exemples : la spécialité {Contrat} n'a aucun document : écart bloquant « déposer au moins 20 contrats ».
- Questions : seuil de « suffisant » (nombre de documents), à mesurer via IAF-E6.
