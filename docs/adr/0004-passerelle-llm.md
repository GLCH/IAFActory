# ADR 0004 : passerelle LLM et routage

Statut : propose (2026-09-26), fournisseurs confirmés le 2026-09-27. Image écrite, jamais construite ni démarrée (Docker arrêté).

## Besoin

Configurer qui utilise quel LLM, mesurer l'usage et les coûts, permettre une orchestration (choix du modèle selon la requête), sans que chaque service embarque ses clés et sa logique fournisseur.

## Décision

1. **Passerelle** : LiteLLM proxy. Selon sa documentation : clés virtuelles par utilisateur ou équipe avec modèles autorisés, budgets et limites de débit (RPM, TPM) ; suivi de la dépense par clé, équipe et utilisateur (`/spend/logs`) ; Postgres requis pour les clés, budgets et le suivi ; image officielle `docker.litellm.ai/berriai/litellm`, version à épingler (la doc déconseille `:latest`). Version retenue : `v1.98.0` (tag existant, vérifié par `docker manifest inspect`).
2. **Orchestration** : `prismhq/jev-router`, greffé en hook de pré-appel LiteLLM. Fichiers relus en entier le 2026-09-26 (25 Ko, MIT, un seul commit `583f0a1d1e0534cda3b6bbfa4b19aa1ec25d73a7` du 2026-09-16, marqué expérimental). Récupéré à ce commit par le Dockerfile, avec vérification du SHA.
3. **Packaging** : image maison `infra/llm-gateway` (comme Fuseki) : image LiteLLM officielle + package `jev_router` + nos `litellm.yaml` et `router.yaml`.
4. **Base dédiée** : base `litellm` et rôle `litellm` dans le Postgres existant (script `02-litellm.sh`), sans accès à la base applicative.
5. **Alias d'usage** : `iaf-extraction`, `iaf-agent`, `iaf-cadrage`, `iaf-judge`, `iaf-auto`. Les services ne connaissent que ces alias.
6. **Clé maître** : détenue par l'API IAFActory seule. Les autres consommateurs reçoivent des clés virtuelles restreintes.
7. **Fournisseurs, hybride (décision 2026-09-27)** : Anthropic (API), Gemini (API Google AI Studio) et Ollama (local). Vérifié dans la documentation LiteLLM : Gemini se déclare `gemini/<modele>` avec une clé simple `GEMINI_API_KEY` (le préfixe sans slash bascule sur Vertex AI, qui demande des identifiants GCP complets : évité) ; Ollama se déclare `ollama_chat/<modele>` avec `api_base` (le préfixe `ollama_chat/` donne de meilleures réponses que `ollama/`, selon la doc, car il utilise l'API de chat plutôt que l'API de complétion).
8. **Choix par agent** : chaque agent choisit son modèle (donc son fournisseur) dans le catalogue de la passerelle au moment de sa définition (US4.1, IAF-25). Un agent qui ne choisit rien reçoit l'alias d'usage par défaut de son rôle.

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

## Vérifié le 2026-09-27 (construction et démarrage réels)

- Le build de l'image réussit (LiteLLM `v1.98.0` + `jev-router` au SHA épinglé) ; `DATABASE_URL` est le bon nom de variable ; les migrations Prisma s'appliquent sans erreur ; le hook `jev_router.hook.proxy_handler_instance` se charge sans erreur avec LiteLLM v1.98.0 (donc `jev-router`, écrit pour `litellm>=1.60.0`, est compatible en pratique malgré l'écart de version) ; `pyyaml` est bien présent dans l'image de base.
- `/health/readiness` répond `{"status":"healthy","db":"connected"}` ; `/v1/models` liste les 12 entrées attendues.
- Appel chat réel via `ollama-local` (Ollama, hors passerelle Anthropic/Gemini faute de clé) : réponse correcte, chargement à froid 25-45 s, réponse à chaud 1-2 s.
- Appel embedding réel via `iaf-embedding` (`ollama/nomic-embed-text`, pas `ollama_chat/`) : dimension **768**, fonctionne du premier coup.
- **`llama3.1:8b` (candidat initial) échoue** sur la machine de dev : `out of memory` en tentant d'allouer un cache KV de 16 Gio (context_length par défaut 131072). Remplacé par `gemma3:4b` avec `num_ctx: 4096` explicite (`extra_body.options.num_ctx`, syntaxe confirmée par la communauté LiteLLM, pas la doc officielle qui ne couvre pas ce cas). Détail et reproduction : `poc/RESULTATS.md`.
- Un appel de chat s'est bloqué durablement (~8 min) sans réponse ni erreur lors d'un usage en boucle (3 appels séquentiels) ; un appel manuel équivalent juste après a répondu en 47 s. Cause non identifiée ; mitigé par `max_tokens` explicite sur chaque appel, pas résolu. À surveiller (US11.4, US13.8).

## Non vérifié

- Les identifiants de modèles Anthropic et Gemini dans `litellm.yaml` (aucune clé disponible pour ce test) : repris de la liste de modèles en vigueur, non appelés en pratique.
- Le repli `iaf-auto` -> `claude-sonnet` de jev-router en cas d'échec (jamais déclenché : aucun appel n'a échoué côté modèle testé).
- La qualité et la latence comparées des trois fournisseurs sur les tâches réelles : seul Ollama a été mesuré.

## Décidé le 2026-09-27 (US5.6)

Budget à deux niveaux, tous deux fixés par l'admin, jamais par le creator : un budget par **projet** (plafond agrégé) et un budget par **agent** (sous-plafond), le second ne pouvant dépasser le premier. Correspond a priori à la hiérarchie équipe (projet) / clé virtuelle (agent) de LiteLLM ; correspondance exacte à vérifier au premier déploiement (US11.3).

## Vérifié le 2026-09-28 (Gemini via Vertex AI, compte de service)

L'utilisateur a fourni un compte de service GCP (pas une clé API Google AI Studio simple, qui restait vide). Ajouté à `infra/llm-gateway/litellm.yaml` : `gemini-vertex-flash` et `gemini-vertex-pro`, prefixe `model: vertex_ai/...`, `vertex_project`/`vertex_location`/`vertex_credentials` (chemin de fichier). Syntaxe vérifiée par recherche web (doc LiteLLM), pas devinée. Fichier de credentials `infra/llm-gateway/gemini-service-account.json`, jamais commité (`.gitignore`), monté en lecture seule dans le conteneur (pas copié dans l'image, même principe que les clés en variables d'environnement).

Testé réellement via `/v1/chat/completions` (`curl` direct sur la passerelle) : réponse `"OK"` reçue, région `us-central1` correcte. Point réel rencontré : les modèles Gemini 2.5 consomment des jetons de "raisonnement" internes qui comptent dans `max_tokens` - un appel avec `max_tokens=10` a renvoyé un contenu vide (`finish_reason: "length"`, 6 jetons de raisonnement, 0 jeton de texte). `site/app/graph.py` relève le défaut à 1200 et traite une réponse vide comme un échec normal (pas un crash) suite à ce constat.

**Décidé le 2026-09-28** : `gemini-vertex-flash` devient le modèle par défaut du site (`extraction_model`/`answer_model`, `site/app/config.py`) à la place d'`ollama-local`, qui causait la plupart des lenteurs et des échecs de délai constatés cette session (plusieurs minutes par document, parfois plus d'une heure sur un document de 91 chunks). Gain mesuré sur un document de test comparable : environ 37 secondes avec Gemini contre plusieurs minutes avec Ollama local. Coût réel mais faible (Flash) ; aucun budget par agent (US11.3) n'est encore appliqué par le site.

## Questions ouvertes

- Qui règle les alias d'usage système : admin seul (tranché en conception 9).
- Le budget projet se règle-t-il en objet LiteLLM `team`, ou reconstruit-il la somme des clés côté API IAFActory ? À trancher au moment d'implémenter US11.3.
- Prix des modèles à renseigner avant d'utiliser `iaf-auto` (source à vérifier) pour les trois fournisseurs.
- Un agent change-t-il de fournisseur sans recréer sa spécialité ni perdre l'historique de ses sessions ?
- Le compte de service Gemini/Vertex AI fourni le 2026-09-28 a-t-il vocation à rester le chemin par défaut, ou une clé Google AI Studio simple (GEMINI_API_KEY, déjà prévue dans litellm.yaml) est-elle préférable à terme (plus simple, pas de notion de projet/région GCP) ?
