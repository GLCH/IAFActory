# EPIC IAF-E11 : passerelle LLM

Jira : IAF-66, stories IAF-68 a IAF-73 (US11.1 a US11.6). Statut : rédigé le 2026-09-26 ; infra écrite (US11.1) mais jamais démarrée. Décision : [ADR 0004](../adr/0004-passerelle-llm.md). Conception : [conception.md](../design/conception.md) section 10.

Objectif : tout appel à un modèle passe par une passerelle qui décide qui utilise quel LLM, applique budgets et limites, et mesure l'usage. Un routeur optionnel choisit le modèle selon la requête.

Questions transverses : fournisseur (API externe ou local) ? qui règle les alias ? prix de référence des modèles ?

## US11.1 Déployer la passerelle en conteneur
- En tant qu'admin de la plateforme, je démarre la passerelle avec le reste de la pile.
- Prérequis : IAF-10 (Postgres), `.env` généré.
- Acceptance criteria : `docker compose up` démarre `llm-gateway` sain (`/health/readiness`) ; image construite depuis LiteLLM `v1.98.0` et jev-router au SHA `583f0a1d1e0534cda3b6bbfa4b19aa1ec25d73a7` (vérifié au build) ; base et rôle `litellm` dédiés ; port lié à 127.0.0.1 ; ne démarre pas sans clé maître ni clé de sel.
- Contexte : écrit le 2026-09-26, jamais construit (Docker arrêté). À valider : chargement du hook avec LiteLLM v1.98.0, `DATABASE_URL`, healthcheck par Python.
- Exemples : `.env` sans `LITELLM_MASTER_KEY` : `docker compose config` échoue avec « voir .env.example ».
- Questions : durcissement non-root et lecture seule (doc de production LiteLLM).

## US11.2 Alias d'usage, catalogue de modèles et choix par agent
- En tant qu'admin, je remappe un alias d'usage vers un autre modèle sans modifier le code des services ; en tant que creator, je choisis le modèle de mon agent dans le catalogue.
- Prérequis : US11.1.
- Acceptance criteria : catalogue hybride confirmé (Anthropic, Gemini, Ollama, décision 2026-09-27) ; les services internes n'utilisent que les alias système (`iaf-extraction`, `iaf-agent`, `iaf-cadrage`, `iaf-judge`, `iaf-auto`, `iaf-embedding`) ; un agent peut référencer un modèle précis du catalogue en plus de ces alias ; alias ou modèle inconnu refusé ; remapper un alias système prend effet sans redéployer les services ; le changement est journalisé (US12.1).
- Contexte : les modèles par défaut par fournisseur restent à confirmer et à mesurer par usage, avec le banc d'évaluation (IAF-47).
- Exemples : `iaf-agent` passe de `claude-sonnet` à `claude-opus` : le service agent n'est pas modifié ; un agent choisit directement `gemini-pro` ou `ollama-local`.
- Questions : l'alias d'embedding dépend du modèle choisi (IAF-13).

## US11.3 Politique : qui utilise quel LLM
- En tant qu'admin, je configure quels consommateurs (services internes, agents, sessions de viewers) peuvent utiliser quels modèles, avec quel budget.
- Prérequis : US11.1, US5.2.
- Acceptance criteria : une clé virtuelle par consommateur avec liste de modèles autorisés (pouvant mélanger les trois fournisseurs), budget avec réinitialisation, limites RPM et TPM ; un appel hors politique est refusé et journalisé ; la clé maître n'est connue que de l'API IAFActory ; interface d'administration IAFActory pour lire et changer la politique. **Décidé le 2026-09-27 (US5.6, granularité précisée le même jour)** : budget à **deux niveaux, tous deux fixés par l'admin** — un budget par **projet** (plafond agrégé sur toutes les dépenses LLM du projet, correspond à un budget d'équipe LiteLLM) et un budget par **agent** (sous-plafond individuel, correspond à un budget de clé virtuelle LiteLLM) ; le budget d'un agent ne peut pas dépasser le budget restant de son projet ; dépasser le plafond projet suspend tous les agents du projet, même ceux dont le budget individuel n'est pas épuisé ; le creator choisit le fournisseur et le modèle (US4.1) mais aucun des deux budgets ne lui est modifiable, affichés en lecture seule.
- Contexte : LiteLLM porte nativement une hiérarchie équipe/clé pour les budgets, selon sa documentation ; à vérifier de bout en bout laquelle correspond exactement à projet/agent ici.
- Exemples : la clé du service d'extraction n'autorise que `iaf-extraction` avec 20 dollars par jour ; un appel à `iaf-judge` avec cette clé renvoie un refus ; un creator sans droit Gemini ne peut pas donner ce modèle à son agent ; un creator qui tente de modifier un budget (projet ou agent) via l'API reçoit 403 ; un projet plafonné à 100 dollars/mois avec un agent à 80 dollars et un autre à 80 dollars : dès que le cumul atteint 100 dollars, les deux agents sont suspendus, même si aucun des deux n'a individuellement atteint 80 dollars.

## US11.4 Routage automatique optionnel
- En tant qu'admin, j'active l'alias `iaf-auto` qui choisit un modèle selon la requête, sans jamais envoyer de contenu à un tiers par défaut.
- Prérequis : US11.1.
- Acceptance criteria : sans `TYPESAFE_API_KEY`, le routeur choisit localement le candidat éligible le moins cher, sans appel réseau externe (test qui échoue si une requête vers `typesafe.ai` part) ; prix réels renseignés et sourcés avant usage ; l'activation de Jev exige une décision écrite et est limitée à des alias sans contenu de documents ; repli sur `claude-sonnet` en cas d'échec.
- Contexte : jev-router envoie à TypeSafe jusqu'à 8 messages de 2000 caractères si la clé est définie (relu dans `deciders.py`). Il est expérimental.
- Exemples : requête avec image sur un candidat sans vision : candidat écarté ; erreur du décideur : modèle de repli.
- Questions : Jev vaut-il d'être activé un jour, vu l'envoi de contexte ?

## US11.5 Secrets fournisseurs et sortie réseau
- En tant qu'admin de la plateforme, je veux que les clés fournisseurs ne fuient pas et que la passerelle ne joigne que les fournisseurs prévus.
- Prérequis : US11.1, `compose.secure.yaml`.
- Acceptance criteria : clés fournisseurs lues depuis des secrets Docker (fichiers), absentes des journaux et des sorties d'API ; en mode durci, sortie limitée à une liste blanche d'hôtes fournisseurs par proxy sortant ; requête vers un hôte hors liste : bloquée et journalisée ; clé de sel stockée hors dépôt et sauvegardée (elle ne peut pas être changée en place).
- Exemples : appel vers `exemple.org` depuis la passerelle en mode durci : refus.

## US11.6 Coûts et usage par consommateur
- En tant qu'admin, je vois combien chaque consommateur consomme (jetons, coût, latence, erreurs) par modèle.
- Prérequis : US11.3.
- Acceptance criteria : dépense agrégée par clé, équipe, utilisateur, modèle et jour depuis les journaux de dépense ; exposée à l'API IAFActory pour le tableau de bord (US12.3) ; chiffres cohérents avec la facture du fournisseur à 5 pour cent près sur une période de test (à mesurer).
- Exemples : le tableau affiche 1,2 million de jetons pour `iaf-extraction` hier, dont 3 pour cent d'erreurs.
