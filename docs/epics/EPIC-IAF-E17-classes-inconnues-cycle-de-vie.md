# EPIC IAF-E17 : pipeline sans reconnaissance de classe (classes inconnues, fusion, promotion, reduction)

Jira : IAF-126 (epic), tâches IAF-127 (US17.1), IAF-128 (US17.2), IAF-129 (US17.3), IAF-130 (US17.4), IAF-131 (US17.5). Statut : rédigé le 2026-10-02 AVANT implémentation (convention DoR du projet), mis à jour après mesure.

Demande explicite (2026-10-02) : "Maintenant nous allons re travailler sur le pipeline sans reconnaissance de classe. Le document n'est pas reconnu, une ontologie agnostique est générée en essayant d'intégrer les corpus connus (étape de recherche corpus matchant le document). On calcule une distance de similarité sémantique avec les autres documents inconnus. Lorsque la similarité augmente (par rapport à une densité de similarité) on merge les deux classes. Si les ontologies structurelles sont les mêmes, on les merge, sinon, on les garde. Lorsque la classe inconnue passe un seuil de nombre de documents intégrés, la classe devient officielle et est intégrée au pipeline pour reconnaissance. On peut réduire l'ontologie en éliminant les noeuds les moins communs (spécifiques à peu de documents)."

## État réel avant ce travail (lu dans le code, pas supposé)

- Chaque document non reconnu crée sa propre classe `status='provisoire'` ; la reconnaissance (`pipeline._find_best_class`) compare un nouveau document à TOUTES les classes, provisoires comprises.
- Après chaque document, `class_merge.check_and_act_on_class` compare la classe à toutes les autres avec des seuils ABSOLUS (`PlatformSettings`, fusion auto désactivée par défaut, suggestion à 0,90) - jamais à une densité de similarité.
- `class_merge.merge_classes` migre les documents et les concepts mais PAS les entités (limite déjà documentée dans son docstring), et ne traite pas les ontologies structurelles : le triple `iafs:acceptsStructure` copié par `ADD <source> TO <cible>` aurait pour sujet le graphe SOURCE (faux).
- Une classe provisoire n'a aucune ontologie structurelle liée (seules les classes importées par `create_class.py` en ont).
- Aucune notion de promotion : une classe provisoire le reste indéfiniment. Aucun élagage des concepts rares (seuls les types d'entités se réduisent, US7.11).

## Décisions de cadrage (paramètres NON calibrés, comme le reste du projet - US7.7 reste le banc de mesure)

- **Classe officielle** = toute classe dont le statut n'est ni `provisoire` ni `fusionnee_dans:...` (donc aussi les classes importées `exemple-importe`) ; une classe promue reçoit `status='officielle'`. **La reconnaissance (US7.4) ne compare plus qu'aux classes officielles.** Un document proche d'une classe provisoire ne s'y rattache plus par reconnaissance : il crée sa classe provisoire, qui fusionne ensuite par la densité de similarité (US17.2).
- **Densité de similarité (US17.2)** : similarité sémantique (`_concept_set_similarity`, plafond sur les embeddings de concepts) entre classes PROVISOIRES ; seuil adaptatif `max(plancher, moyenne + k·écart-type)` calculé sur l'ensemble des paires de classes provisoires (au moins `merge_density_min_pairs` paires, sinon le plancher seul). Défauts : plancher 0,50, k = 1,0, 3 paires.
- **Seuil de promotion (US17.4)** : `official_class_min_documents` = 3 documents intégrés.
- **Réduction (US17.5)** : un concept est rare s'il est mentionné par moins de `ontology_reduction_min_support` documents (défaut 2) ; seuls les concepts INDUITS (au moins une mention par un document) sont candidats - les concepts importés d'une ontologie (aucune mention) sont protégés, sinon l'ontologie Vin ou C2SIM serait vidée.
- Tous ces paramètres vivent dans `app/config.py` (surchargeables par variables d'environnement) et sont affichés en lecture seule sur `/creator/settings` : pas de migration de base pour ce premier jet.

## US17.1 Recherche de corpus connus lors de la génération de l'ontologie agnostique
- En tant que système, quand un document n'est pas reconnu, je cherche les corpus officiels les plus proches et j'aligne l'ontologie induite sur leur vocabulaire, pour que des ontologies de documents du même domaine restent comparables et fusionnables.
- Prérequis : US7.4 (score), concepts avec embeddings (corrigé le 2026-10-01).
- Acceptance criteria : étape tracée "Recherche de corpus proches" (top 3 classes officielles avec score sémantique) ; relation `(:DocumentClass)-[:NEAR_CORPUS {semantic_score}]->(official)` ; le normaliseur de concepts de la classe provisoire est AMORCÉ avec les concepts (et leurs embeddings stockés, pas recalculés) du corpus officiel le plus proche si son score sémantique dépasse `corpus_seed_min_similarity` (défaut 0,30) ; carte "Corpus proches" sur l'écran classe.
- Exemples : un PDF de cuisine non reconnu face aux classes Vin et C2SIM : scores faibles, rien n'est repris ; un document de vin partiel (score 0,40) reprend les libellés "Zinfandel", "WineBody" déjà connus au lieu de créer des quasi-doublons.

## US17.2 Fusion de classes inconnues par densité de similarité
- En tant que système, après chaque document, je compare la classe provisoire aux autres classes provisoires et je fusionne quand sa similarité dépasse la densité de similarité ambiante.
- Prérequis : US17.1, `class_merge.merge_classes`.
- Acceptance criteria : seuil adaptatif ci-dessus (valeur et échantillon tracés dans l'étape) ; fusion de la plus petite vers la plus grande classe ; **les entités et leurs relations sont désormais migrées** (fusion des entités de même nom, `apoc.refactor.mergeNodes`) - la limite documentée de `merge_classes` est levée pour ce cas ; boucle jusqu'à stabilité ; jamais de fusion automatique avec une classe officielle ; étapes tracées sur `/creator/processes`.
- Exemples : trois recettes de cuisine déposées l'une après l'autre ; la 2e se fond dans la 1re (similarité > seuil), un document d'astronomie reste une classe provisoire distincte.

## US17.3 Ontologies structurelles à la fusion : mêmes -> fusionnées, différentes -> conservées
- En tant que système, une classe provisoire est liée à l'ontologie structurelle qui correspond le mieux à son premier document, et la fusion de deux classes réunit leurs ontologies structurelles.
- Prérequis : IAF-125 (`structure_matcher`), `iafs:acceptsStructure` (0..n).
- Acceptance criteria : à la création d'une classe provisoire, lien vers l'ontologie du meilleur match si son score atteint le seuil du pré-filtre ; à la fusion, union des deux ensembles (identiques -> une seule entrée, différentes -> les deux conservées) ET sujet des triples corrigé vers le graphe cible ; l'écran classe liste les ontologies structurelles de la classe fusionnée.
- Exemples : deux classes liées à `structure-word` -> une classe, une ontologie ; une classe Word + une classe PDF -> une classe qui accepte les deux.

## US17.4 Promotion d'une classe inconnue en classe officielle
- En tant que système, quand une classe provisoire atteint le seuil de documents intégrés, elle devient officielle et entre dans la reconnaissance.
- Prérequis : US17.2.
- Acceptance criteria : `official_class_min_documents` documents -> `status='officielle'`, préfixe "Provisoire - " retiré du nom, documents Postgres passés à `recognized` (pas de nouvelle valeur d'enum, pas de migration) ; action manuelle "Promouvoir maintenant" sur l'écran classe ; la promotion est tracée ; un document ultérieur proche de cette classe est RECONNU (statut `recognized`) au lieu de créer une classe provisoire.
- Exemples : la 3e recette fusionnée porte la classe à 3 documents -> "Recettes de cuisine" est officielle ; la 4e recette est reconnue directement.

## US17.5 Réduction de l'ontologie : éliminer les concepts les moins communs
- En tant que creator, je peux supprimer les concepts de l'ontologie d'une classe spécifiques à peu de documents.
- Prérequis : classe avec au moins `official_class_min_documents` documents (sinon tout concept serait "rare").
- Acceptance criteria : page `/creator/classes/{id}/reduce-ontology` avec seuil de support réglable, aperçu AVANT écriture (concepts retirés, conservés, protégés), puis application (Neo4j + `owl:Class` Fuseki, relations `SUBCLASS_OF` retirées) ; les concepts jamais mentionnés par un document (importés) sont protégés.
- Exemples : classe de 5 documents, concept "Plat" mentionné par 1 seul document -> retiré ; "Zinfandel" importé sans mention -> protégé.

## Questions ouvertes
- Valeurs par défaut (plancher 0,50, k = 1,0, 3 documents, support 2) à calibrer sur un jeu annoté (US7.7).
- Faut-il autoriser la fusion automatique d'une classe provisoire dans une classe OFFICIELLE très proche (aujourd'hui : suggestion seulement, via `class_merge`) ?
- Le nom d'une classe promue reste dérivé du titre de son premier document : un renommage assisté par LLM serait utile.

## Mise en oeuvre et mesure réelle (2026-10-02)

Implémenté : `class_lifecycle.py` (densité, fusion, promotion, absorption, ré-évaluation), `class_reduction.py`, recherche de corpus + amorçage du normaliseur dans `pipeline.py`, migration des entités dans `class_merge.merge_classes`, union des ontologies structurelles dans `ontology.merge_class_ontology`, écrans (statut/promotion/corpus proches sur l'écran classe, `/creator/classes/{id}/reduce-ontology`, bouton « Réévaluer les classes inconnues » sur `/creator/corpus`, paramètres en lecture seule). Tests 49/49 (+8 : seuil de densité, plan et application de la réduction, union des ontologies structurelles avec sujet corrigé, suppression de concept multi-URI).

**Scénario RÉEL de bout en bout** (documents Word générés par Gemini, déposés UN PAR UN par la vraie route du site, worker + cycle de vie réels) :
1. 4 recettes de cuisine -> toutes RECONNUES comme **Vin** (60-64 %) : structure 68-96 %, sémantique 32-53 %. **Constat important** : avec le seuil de reconnaissance abaissé à 50 % par l'utilisateur, la moyenne structurel+sémantique laisse passer un document structurellement proche et sémantiquement voisin (la gastronomie est proche du vin, et Vin contient déjà "Plat", "Gastronomie"). Ces documents ont été retirés de Vin (documents + concepts/entités propres) ; aucune porte sémantique minimale n'a été ajoutée - à décider.
2. Documents d'astronomie : sémantique vs Vin 4-12 % -> classes provisoires. Étape « Recherche de corpus proches » : Vin 4-12 %, rien repris.
3. **Plancher de fusion ramené de 0,50 à 0,30 après mesure** : similarité entre documents d'astronomie 36 à 51 %, entre domaines sans rapport 0 à 12 % (droit vs astronomie 0 %) ; à 50 % deux documents d'astronomie restaient isolés. La densité (moyenne 15-21 %, écart-type 17-22 %) est inférieure au plancher sur ce petit échantillon : c'est le plancher qui décide, la densité ne devient discriminante qu'avec davantage de classes.
4. Après ré-évaluation : `astro-1`, `astro-3`, `astro-4` fusionnent (3 documents, promotion automatique -> classe **officielle**, 116 entités migrées, 33 concepts) ; `astro-2`, restée isolée parce que sa voisine était devenue officielle, est absorbée par l'étape ajoutée « rattachement à une classe officielle » (même formule et même seuil que la reconnaissance). Le document de droit reste une classe provisoire (1/3).
5. 5e document d'astronomie déposé : **RECONNU** dans la classe promue (67 %, statut `recognized`), au lieu de créer une classe provisoire.
6. Réduction de l'ontologie sur la classe promue (5 documents, support minimal 2) : 42 concepts -> 10 conservés, 32 retirés. **Bug trouvé en vérifiant Neo4j contre Fuseki** : Fuseki gardait 43 `owl:Class` - après fusion, un libellé peut porter plusieurs URI (une par classe d'origine, alors que Neo4j n'a qu'un noeud) et la suppression recalculait une seule URI. Corrigé (`Concept.uri` stockée + suppression de toute `owl:Class` du libellé, y compris pour la suppression manuelle d'un concept), Fuseki resynchronisé (mêmes 10 libellés), test de non-régression.

Limites assumées : (a) aucun des documents de test n'a atteint le seuil du pré-filtre structurel (pas de « Chapitre/Discussion/Conclusion »), donc aucune classe provisoire n'a été liée à une ontologie structurelle dans ce scénario - la fusion d'ontologies structurelles (identiques fusionnées, différentes conservées) est vérifiée par un test sur le vrai Fuseki, pas par ce scénario ; (b) le nom d'une classe promue vient du nom de fichier de son premier document (`astro-3.docx`) ; (c) la densité reste non calibrée faute de classes ; (d) la réduction considère comme « protégé » tout concept sans mention, y compris un concept dont les documents ont été supprimés.

## Jeu d'essai rejouable (2026-10-02)

Les documents du scenario, les classes de depart, les ontologies semantique et structurelles et l'etat observe sont dans `corpus/` (voir `corpus/README.md`). Rejeu : `site/scripts/replay_learning.py <scenario> --seed` (depot par la vraie route du site, trace du cycle de vie, rapport JSON). Verifie : plan (`--dry-run`) des 4 scenarios, connexion, detection des seeds, refus 409 des doublons. NON verifie : un rejeu complet depuis un etat propre (`--reset-learned`), qui supprimerait les classes apprises de l'instance courante.

**Rejeu complet depuis un etat propre (2026-10-02, `corpus/references/replay-2026-10-02.json`)** : valide. Reset des classes apprises puis scenario `apprentissage-classe-inconnue` : astro-1 provisoire (29 %), astro-2 fusionne avec astro-1 des son depot (similarite 39 % > plancher 30 %, ontologies structurelles identiques fusionnees), droit-1 provisoire (similarite 0 %), astro-3 fusionne et promotion a 3 documents (classe officielle `astro-1.docx`), astro-4 et astro-5 reconnus (60 % et 68 %). Deux constats : (1) a la reevaluation, droit-1 est absorbe par la classe astronomie (structure Word identique, semantique faible, score combine >= 0,50) : meme defaut que les recettes reconnues comme Vin, remede envisage = porte semantique minimale, decision a l'utilisateur ; (2) corrige : un document absorbe dans une classe officielle restait `provisional` (statut desormais `recognized`, `class_lifecycle._try_absorb_into_official`). Observation d'exploitation : la suppression d'une classe de 5 documents par la route du site a pris plusieurs minutes (Neo4j charge) ; non analyse.
