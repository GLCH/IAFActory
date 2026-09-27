# EPIC IAF-E15 : agent codeur

Jira : IAF-98 (stories ci-dessous). Statut : rédigé le 2026-09-27, rien d'implémenté. Décision : [ADR 0008](../adr/0008-agent-codeur.md). Conception : [conception.md](../design/conception.md) section 14.3.

Objectif : une fois l'architecture d'un projet validée (US8.7), un agent codeur - Claude Code via le Claude Agent SDK, pas un nouveau prompt maison - transforme chaque user story du backlog en une proposition de code réelle, revue par un humain avant fusion.

Questions transverses : offre exacte du Claude Agent SDK pour un usage multi-creators hébergé ; hébergement de l'espace de travail isolé ; qui paie l'exécution.

## US15.1 Déclencher l'agent codeur sur une user story
- En tant que creator, je demande à l'agent codeur de traiter une user story de mon backlog, une fois l'architecture validée.
- Prérequis : US8.6 (backlog), US8.7 (architecture validée).
- Acceptance criteria : l'agent ne peut être déclenché qu'après validation explicite de l'architecture par le creator ; l'entrée fournie à l'agent est la story complète (description, prérequis, critères d'acceptation) et le document d'architecture, jamais une phrase libre reformulée ; une story sans critère d'acceptation est refusée (cohérent avec US6.1).
- Contexte : intégration du Claude Agent SDK, pas un prompt à concevoir (ADR 0008).
- Exemples : une story sans critère d'acceptation renvoie une erreur explicite plutôt que de lancer l'agent.
- Questions : traitement d'une story à la fois, ou plusieurs en parallèle avec des branches distinctes ?

## US15.2 Espace de travail isolé et git
- En tant qu'admin de la plateforme, je veux que l'agent codeur ne puisse toucher que le dépôt du projet concerné, jamais le reste de l'infra.
- Prérequis : US15.1.
- Acceptance criteria : environnement jetable par exécution (conteneur ou VM), dépôt du projet cloné, aucun accès réseau vers Postgres/Neo4j/Fuseki/la passerelle LLM d'IAFActory sauf si le service du creator en dépend explicitement comme dépendance externe de son propre projet ; travail sur une branche dédiée ; ouverture d'une pull request ; **aucune fusion automatique, dans tous les cas**.
- Contexte : traité comme un connecteur en écriture vers un dépôt externe (GitHub/GitLab), donc soumis à l'approbation de l'admin comme tout connecteur (US9.1).
- Exemples : l'agent tente une requête réseau vers l'IP interne de Postgres : refusée et journalisée (même principe que US9.3 pour les connecteurs).

## US15.3 Revue humaine et rapport d'échec
- En tant que creator, je vois ce que l'agent codeur a produit avant toute fusion, et pourquoi il a échoué s'il a échoué.
- Prérequis : US15.1, US15.2.
- Acceptance criteria : la sortie normale est une pull request avec un résumé des changements et leur lien avec la story ; en cas d'échec (ambiguïté, dépendance manquante, test qui ne passe pas), un rapport explicite est produit, jamais un silence ; le creator peut renvoyer la story à l'agent Backlog (US8.6) pour reformulation.
- Exemples : critère d'acceptation ambigu : l'agent signale l'ambiguïté précisément plutôt que de deviner.
- Questions : qui d'autre que le creator peut revoir et fusionner (délégation à un tiers) ?

## US15.4 Coût et budget
- En tant qu'admin, je veux que l'exécution de l'agent codeur respecte le budget du projet.
- Prérequis : US15.1, US11.3 (budget à deux niveaux, projet et agent).
- Acceptance criteria : le coût (jetons, temps machine) est imputé au budget du projet du creator ; le budget épuisé bloque un nouveau déclenchement, avec un message explicite ; le coût réel est visible au tableau de bord (US12.3).
- Questions : le temps machine de l'espace de travail isolé se facture-t-il séparément des jetons du modèle ?
