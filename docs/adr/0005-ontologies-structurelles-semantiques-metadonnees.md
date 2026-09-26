# ADR 0005 : ontologies structurelles et sémantiques, métadonnées

Statut : propose (2026-09-26).

## Décision

1. **Deux familles d'ontologies, indépendantes** :
   - **sémantique** : décrit le contenu (types d'entités, de relations, attributs), rattachée aux domaines de la taxonomie commune (ADR 0002) ;
   - **structurelle** : décrit l'organisation du contenu (types d'éléments et agencement). Un vocabulaire de base commun ([ontologies/structure/iaf-structure-base.ttl](../../ontologies/structure/iaf-structure-base.ttl)) que l'ontologie structurelle de chaque classe spécialise.
2. **Une classe documentaire** réunit un profil structurel et un ou plusieurs domaines sémantiques.
3. **Reconnaissance sur deux axes** avec une décision croisée (conception 4.2) : la structure se reconnaît sans LLM, la sémantique avec.
4. **Métadonnées** conservées quand le document en porte, normalisées plus valeur brute et source ; jamais inventées ; signal faible de reconnaissance ; traitées comme données personnelles et non fiables.
5. **Structure dans le graphe** : les éléments structurels sont des nœuds (`StructElement`) reliés aux chunks ; un fait sémantique est donc situé dans le document.

## Pourquoi deux familles

- La forme se reconnaît à bas coût et de façon déterministe : un pré-filtre gratuit avant les appels LLM.
- Le même contenu peut arriver en Word ou en PowerPoint : une seule ontologie mélangeant forme et contenu forcerait deux classes pour un même sujet.
- Situer chaque fait (section, diapositive) améliore la provenance des réponses.

## Conséquences

- Plus d'objets à gouverner : profils structurels induits, variantes, versions. Anti-prolifération à mesurer (US7.5).
- Le squelette dépend de la qualité de l'analyse structurelle (US3.8) : un titre mal détecté fausse la reconnaissance structurelle. Mesure sur documents réels obligatoire.
- L'ontologie de base n'est pas encore validée par un parseur RDF ni chargée dans Fuseki.

## Risques

- **Métadonnées hostiles ou fausses** : jamais décisives seules, jamais suivies comme instruction.
- **Données personnelles** dans l'auteur et le dernier modificateur : mêmes droits d'accès que le document, rétention (E12), exclues des prompts par défaut.
- **Fuite d'information interne** par des métadonnées (chemins, noms de machines, historique de révision) : seuls les champs normalisés utiles sont exposés ; la valeur brute reste réservée au propriétaire du projet.

## Non vérifié

- Le nom et l'accessibilité des propriétés selon le format et l'outil d'analyse (Docling n'a pas été testé sur ce point).
- La validité de syntaxe de l'ontologie de base (validation prévue par rdflib en CI, jamais exécutée).

## Questions ouvertes

- Profils structurels propres à chaque classe (hypothèse) ou partageables ?
- Une variante structurelle appartient-elle à la même classe (hypothèse) ?
- Quelles métadonnées le creator veut-il exploiter ?
