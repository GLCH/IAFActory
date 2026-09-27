# EPIC IAF-E8 : comprendre le besoin du creator

Jira : IAF-38, stories IAF-48 a IAF-51 (US8.1 a US8.4), IAF-99 a IAF-101 (US8.5 a US8.7, nouvelles). Statut : rédigé le 2026-09-26 ; étendu le 2026-09-27 avec la chaîne complète d'agents produit (conception.md section 14) : Interprète, Parcours, Backlog, Architecte, puis l'agent codeur (IAF-E15, hors de cet epic car de nature différente).

Objectif : le creator exprime un besoin ; le service le comprend, reconnaît ou crée le projet concerné, aide à définir ses parcours utilisateurs, produit un backlog Jira au format Definition of Ready, propose une architecture, puis prépare l'agent de projet (spécialité, sources, tests). Le creator valide chaque étape.

Questions transverses : le creator est-il technique ? quel niveau de guidage ? un besoin peut-il donner plusieurs agents ou plusieurs projets ?

## US8.1 Exprimer un besoin, reconnaître ou créer le projet (agent Interprète)
- En tant que creator, je décris en langage naturel ce que je veux obtenir ; l'agent Interprète détermine si cela concerne un projet que j'ai déjà ou si un nouveau projet doit être créé.
- Prérequis : IAF-30 (connexion).
- Acceptance criteria : le brief est enregistré et versionné ; champs libres plus trois champs guidés : qui utilise le résultat, quelle décision il éclaire, exemples de bonnes réponses ; un brief vide est refusé ; **ajouté le 2026-09-27** - l'agent Interprète compare le besoin aux projets existants du creator (par similarité de description, même principe que la reconnaissance documentaire section 4 mais appliqué aux projets) et propose soit de continuer un projet existant (avec explication du rapprochement), soit d'en créer un nouveau ; aucun projet n'est créé sans validation explicite du creator.
- Contexte : alias `iaf-cadrage`. Avant le 2026-09-27, le projet était supposé déjà choisi (IAF-31) avant d'exprimer un besoin ; l'ordre s'inverse - le besoin peut précéder le projet.
- Exemples : « Je veux répondre aux questions des commerciaux sur les clauses de nos contrats » est enregistré avec utilisateurs cibles « commerciaux » ; si le creator a déjà un projet « Contrats fournisseurs », l'agent Interprète le propose plutôt que d'en créer un nouveau.
- Questions : seuil de similarité pour proposer un projet existant plutôt qu'un nouveau (à calibrer, même prudence que IAF-47) ?

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

## US8.5 Aider à créer les parcours utilisateurs (agent Parcours)
- En tant que creator, je veux que l'assistant m'aide à définir les parcours utilisateurs de mon projet, à partir du besoin cadré.
- Prérequis : US8.2.
- Acceptance criteria : sortie structurée par parcours (acteur, étapes, ce qui déclenche chaque étape, ce qu'elle produit) ; au moins un parcours proposé, le creator peut en ajouter, modifier, en retirer ; chaque parcours validé individuellement ; rendu texte structuré en v1 (pas de diagramme exigé).
- Contexte : alias `iaf-cadrage`. Nouvelle capacité (conception.md section 14.2), aucune couverture antérieure.
- Exemples : besoin « répondre aux commerciaux sur les clauses » donne un parcours « Commercial cherche une clause avant un rendez-vous client » avec ses étapes (poser la question, recevoir la réponse sourcée, vérifier la source).
- Questions : un diagramme (rendu visuel) est-il attendu dès cette version, ou le texte structuré suffit ?

## US8.6 Créer le backlog Jira au format Definition of Ready (agent Backlog)
- En tant que creator, je veux que mes parcours validés deviennent un projet Jira avec des epics et des user stories complètes, sans avoir à les rédiger moi-même.
- Prérequis : US8.5.
- Acceptance criteria : création d'un projet Jira (ou réutilisation si le creator en désigne un existant) ; chaque parcours ou groupe de parcours devient un epic ; chaque étape ou capacité devient une user story avec, dans cet ordre : description la plus claire et contextualisée possible, puis prérequis, puis critères d'acceptation précis permettant de valider la story (même structure que celle suivie pour IAFActory lui-même) ; rien n'est créé dans Jira avant validation explicite du creator sur le contenu proposé ; connecteur Jira en écriture soumis à l'approbation de l'admin (US9.1, tout connecteur, même déjà catalogué).
- Contexte : alias `iaf-produit` (raisonnement plus soutenu que `iaf-cadrage`, ajouté à `infra/llm-gateway/litellm.yaml`). Formalise pour le creator la méthode déjà suivie manuellement pour IAFActory (mémoire du projet : epics au format Definition of Ready).
- Exemples : le parcours « Commercial cherche une clause » donne une story « Rechercher une clause par mot-clé » avec sa description, le prérequis « documents contractuels ingérés », et les critères « la réponse cite le document et la clause exacte ; latence sous 5 secondes ».
- Questions : identifiants du connecteur Jira du creator, ou compte de service de la plateforme (même question que IAF-E15 US15.1 pour l'agent codeur, à trancher ensemble) ?

## US8.7 Concevoir l'architecture du service (agent Architecte)
- En tant que creator, je veux une proposition d'architecture pour mon projet, avant que du code ne soit écrit.
- Prérequis : US8.6.
- Acceptance criteria : document d'architecture (composants, magasins de données, intégrations, dépendances vers le reste d'IAFActory le cas échéant) sur le modèle des ADR déjà utilisés dans ce dépôt ; explicite sur ce qui est vérifié contre ce qui est une hypothèse (même exigence que le reste du projet : ne pas deviner) ; rien n'est engagé avant validation du creator ; l'architecture validée est ce qui déclenche l'agent codeur (IAF-E15, US15.1).
- Contexte : alias `iaf-produit`.
- Exemples : pour le projet « Contrats fournisseurs », l'architecture propose de réutiliser le graph RAG et la passerelle LLM existants d'IAFActory, sans magasin de données supplémentaire.
- Questions : qui valide si l'architecture a des implications de coût ou de sécurité dépassant la compétence du creator (même question que conception.md 14.4) ?
