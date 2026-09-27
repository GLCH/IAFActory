# ADR 0008 : agent codeur (intégration Claude Code / Agent SDK)

Statut : proposé (2026-09-27). Rien n'est implémenté. Décision explicite de l'utilisateur : « il nous faut cet agent codeur (toi) » - l'agent codeur d'IAFActory n'est pas un nouveau prompt LLM à concevoir, c'est une intégration de Claude Code (ou du Claude Agent SDK sous-jacent).

## Pourquoi ce n'est pas un agent produit comme les autres (section 14.3 de la conception)

Les agents Interprète, Parcours, Backlog et Architecte (US8.1bis, 8.5-8.7) répondent à partir d'un contexte : une conversation, un besoin, un backlog. L'agent codeur doit en plus :

- lire et écrire des fichiers dans un dépôt de code réel,
- utiliser git (branches, commits, pull requests),
- exécuter des commandes (installer des dépendances, lancer des tests),
- itérer sur ses propres erreurs (un test qui échoue, une erreur de compilation).

Aucun de ces besoins ne se résout par un appel de complétion à travers la passerelle LLM (ADR 0004) : celle-ci route des requêtes de chat/complétion vers un modèle, elle ne fournit ni système de fichiers, ni terminal, ni git à ce modèle.

## Décision

1. **Produit** : le rôle d'agent codeur est rempli par Claude Code, exécuté via le **Claude Agent SDK** (le SDK qui fait tourner Claude Code lui-même). C'est un choix de produit assumé, pas seulement technique : la qualité de ce que produit cet agent est directement celle de Claude Code.
2. **Déclenchement** : après validation par le creator de l'architecture (US8.7), pas avant. Chaque user story du backlog (US8.6) devient une tâche pour l'agent codeur.
3. **Espace de travail isolé** : un environnement dédié par exécution (conteneur ou machine virtuelle jetable), avec le dépôt de code du projet cloné, jamais un accès direct au reste de l'infra IAFActory (Postgres, Neo4j, Fuseki restent hors de portée sauf si le projet du creator en a explicitement besoin comme dépendance de son propre service).
4. **Git et revue humaine obligatoire** : l'agent travaille sur une branche, ouvre une pull request, ne fusionne jamais lui-même. Le creator (ou une personne qu'il désigne) revoit et fusionne. Aucune fusion automatique, quelle que soit la confiance mesurée par ailleurs (cohérent avec la règle du projet : les bénéfices annoncés se valident, ne se supposent pas).
5. **Entrée** : la user story (description, prérequis, critères d'acceptation, produits par l'agent Backlog) et le document d'architecture (agent Architecte), pas une simple phrase libre.
6. **Sortie** : une pull request, ou un rapport explicite d'échec avec la cause (jamais un silence).

## Ce qui reste à vérifier avant tout code

- Quelle offre exacte du Claude Agent SDK correspond à un déploiement multi-utilisateurs, hébergé par IAFActory pour le compte de ses creators (par opposition à un usage local comme celui de cette session) : à documenter précisément avant l'implémentation, pas supposé ici.
- Coût par exécution (jetons, temps machine) et qui le paie : logiquement le budget LLM du projet (US11.3, budget par projet et par agent), à confirmer.
- Hébergement de l'espace de travail isolé : machine de la plateforme, ou compte cloud du creator ?
- Intégration Jira : l'agent codeur lit-il directement les tickets (API Jira), ou reçoit-il un texte déjà extrait par l'agent Backlog ? Proposition : la deuxième option, pour ne pas dupliquer un connecteur Jira dans deux agents différents.
- Sécurité : mêmes principes que les connecteurs (ADR 0003) - l'espace de travail de l'agent codeur est un cas de connecteur en écriture vers un dépôt de code externe (GitHub, GitLab...), donc soumis à l'approbation de l'admin comme tout connecteur (US9.1 révisée le 2026-09-27).

## Alternatives écartées

- Un agent maison basé sur un modèle générique de la passerelle avec des outils codés à la main (lecture/écriture de fichiers, exécution de commandes) : réinventerait ce que le Claude Agent SDK fournit déjà, avec une qualité et une maintenance moins bonnes.
- Génération de code sans revue humaine : jamais retenu, cohérent avec la règle du projet (validation avant tout usage réel).

## Questions ouvertes

- Le creator peut-il choisir un fournisseur alternatif pour cet agent (comme pour les agents de projet, US4.1), ou l'agent codeur est-il toujours Claude, sans alternative ? Hypothèse actuelle : toujours Claude, car le choix du produit est justement d'utiliser Claude Code.
- Que se passe-t-il si l'agent codeur ne peut pas terminer une story (ambiguïté, dépendance manquante) ? Remontée au creator, à l'agent Backlog pour reformuler, ou aux deux ?
