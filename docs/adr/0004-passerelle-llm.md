# ADR 0004 : passerelle LLM et routage

Statut : propose (2026-09-26). Image écrite, jamais construite ni démarrée (Docker arrêté).

## Besoin

Configurer qui utilise quel LLM, mesurer l'usage et les coûts, permettre une orchestration (choix du modèle selon la requête), sans que chaque service embarque ses clés et sa logique fournisseur.

## Décision

1. **Passerelle** : LiteLLM proxy. Selon sa documentation : clés virtuelles par utilisateur ou équipe avec modèles autorisés, budgets et limites de débit (RPM, TPM) ; suivi de la dépense par clé, équipe et utilisateur (`/spend/logs`) ; Postgres requis pour les clés, budgets et le suivi ; image officielle `docker.litellm.ai/berriai/litellm`, version à épingler (la doc déconseille `:latest`). Version retenue : `v1.98.0` (tag existant, vérifié par `docker manifest inspect`).
2. **Orchestration** : `prismhq/jev-router`, greffé en hook de pré-appel LiteLLM. Fichiers relus en entier le 2026-09-26 (25 Ko, MIT, un seul commit `583f0a1d1e0534cda3b6bbfa4b19aa1ec25d73a7` du 2026-09-16, marqué expérimental). Récupéré à ce commit par le Dockerfile, avec vérification du SHA.
3. **Packaging** : image maison `infra/llm-gateway` (comme Fuseki) : image LiteLLM officielle + package `jev_router` + nos `litellm.yaml` et `router.yaml`.
4. **Base dédiée** : base `litellm` et rôle `litellm` dans le Postgres existant (script `02-litellm.sh`), sans accès à la base applicative.
5. **Alias d'usage** : `iaf-extraction`, `iaf-agent`, `iaf-cadrage`, `iaf-judge`, `iaf-auto`. Les services ne connaissent que ces alias.
6. **Clé maître** : détenue par l'API IAFActory seule. Les autres consommateurs reçoivent des clés virtuelles restreintes.

## Ce que fait jev-router (constaté dans le code)

- Ne réagit qu'aux modèles listés dans `aliases` de `router.yaml` ; les autres requêtes passent intactes.
- Filtre les candidats (image, longueur de sortie, outils), puis choisit : **sans `TYPESAFE_API_KEY`**, le moins cher (règle locale, aucun appel externe) ; **avec la clé**, il interroge `https://api.typesafe.ai/v1/systemone`.
- Tout échec retombe sur le modèle de repli.
- Limite constatée : sans prix (`null`), « le moins cher » est le premier de la liste ; nos candidats sont donc classés du moins cher au plus cher, en attendant des prix vérifiés.

## Risque de confidentialité, et décision

Avec Jev activé, un résumé de la requête (jusqu'à 8 messages de 2000 caractères chacun) est envoyé à TypeSafe, un tiers. Dans IAFActory, les requêtes contiennent des extraits de documents techniques : ce serait une fuite de données vers un tiers non prévu.

**Jev est désactivé par défaut** : `compose.yaml` ne définit jamais `TYPESAFE_API_KEY`. L'activer exige une décision explicite documentée, limitée à des alias sans document (ex. classification de requêtes sans contexte), jamais à `iaf-extraction`, `iaf-agent` ou `iaf-judge`. Un test automatisé de la passerelle vérifiera qu'aucune requête sortante vers `typesafe.ai` n'a lieu sans cette clé (US11.4).

## Autres risques et mesures

- **Maturité** : jev-router est expérimental (« APIs and config will change »), un commit, 13 étoiles. Épinglage au SHA, code relu, surface faible (une classe de hook). Si le projet devient un frein, on retire le hook : le reste de la passerelle est LiteLLM seul.
- **Clés fournisseurs** : fournies par variable d'environnement au démarrage, jamais dans l'image. Passage aux secrets Docker (fichier monté) prévu avec US11.5.
- **Contenu des journaux** : le réglage évitant de stocker les prompts dans les journaux de dépense doit être identifié dans la doc LiteLLM et testé (US12.4).
- **Sortie réseau** : la passerelle est sur le réseau `edge` en mode durci ; une liste blanche d'hôtes fournisseurs par proxy sortant reste à faire (US11.5).
- **Image de base** : utilisateur, système de fichiers et santé `/health/readiness` documentés par LiteLLM ; le durcissement (non-root, lecture seule) est décrit dans sa doc de production et reste à appliquer et tester.

## Alternatives écartées

- Pas de passerelle (clés dans chaque service) : aucune politique centrale, aucun suivi unifié.
- Passerelle écrite maison : coût de maintenance élevé pour des fonctions déjà couvertes.
- Autres passerelles (Portkey, Kong AI Gateway) : citées dans des comparatifs, non évaluées ici.

## Non vérifié

- Le build de l'image, le démarrage de LiteLLM avec notre config, le chargement du hook, `DATABASE_URL` (nom de variable d'après l'usage courant, la doc consultée parle de « Database URL »), la présence de `pyyaml` dans l'image de base : jev-router le déclare explicitement en dépendance ; LiteLLM en a besoin pour lire son propre `config.yaml`, donc il devrait être présent, mais cela se vérifie au build (sinon l'ajouter dans le Dockerfile).
- La compatibilité de `jev-router` (écrit pour `litellm>=1.60.0`) avec `v1.98.0`.
- Les identifiants de modèles Anthropic dans `litellm.yaml` (repris de la liste de modèles en vigueur, à confirmer au premier appel).

## Questions ouvertes

- Fournisseur : API externe ou local uniquement ? (question 2 de la conception)
- Qui règle les alias : admin seul ou aussi creators pour leurs agents ? (US5.6)
- Prix des modèles à renseigner avant d'utiliser `iaf-auto` (source à vérifier).
