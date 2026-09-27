# EPIC IAF-E9 : connecteurs sécurisés

Jira : IAF-39, stories IAF-52 a IAF-57 (US9.1 a US9.6). Statut : rédigé le 2026-09-26. Décision : [ADR 0003](../adr/0003-connecteurs-securises.md). Rien n'est implémenté.

Objectif : brancher des sources externes sans exposer d'identifiants ni ouvrir le réseau interne.

Questions transverses : sources à brancher en premier ? coffre externe ? antivirus ? où s'exécutent les connecteurs ?

Décidé le 2026-09-27 (US5.6) : toute activation de connecteur passe par l'approbation de l'admin, y compris pour un type déjà catalogué (US9.1 révisée).

## US9.1 Registre de connecteurs, soumis à approbation
- En tant que creator, je déclare un connecteur (type, source, portée) dans mon projet ; il reste inactif jusqu'à l'accord de l'admin.
- Prérequis : IAF-31 (projets), IAF-30.
- Acceptance criteria : un connecteur appartient à un projet et à un creator ; types déclarés dans un catalogue fermé ; **décidé le 2026-09-27 (US5.6)** : tout connecteur créé est à l'état `brouillon`, passe en `en attente` quand le creator demande l'activation, puis `approuvé` ou `rejeté` par un admin — même quand le type est déjà approuvé ailleurs, dans le même projet ou pour un autre creator ; un connecteur non approuvé ne peut ni lire ni écrire ; rejet motivé et journalisé ; lecture seule par défaut une fois approuvé ; un creator ne voit que ses connecteurs ; création, demande, décision et modification journalisées.
- Exemples : le creator A ne peut ni lister ni utiliser le connecteur du creator B (404) ; un connecteur `en attente` renvoie 409 sur toute tentative de synchronisation ; approuver un deuxième connecteur du même type pour le même creator exige une nouvelle décision de l'admin.

## US9.2 Identifiants chiffrés
- En tant que creator, je fournis des identifiants qui ne ressortiront jamais.
- Prérequis : US9.1.
- Acceptance criteria : chiffrement par enveloppe, clé maître lue depuis un secret Docker ; identifiants en écriture seule (aucune route ne les renvoie) ; absents des journaux et des prompts (test automatisé par recherche d'une valeur sentinelle) ; rotation possible sans recréer le connecteur.
- Contexte : interface `SecretStore` pour changer de coffre plus tard (ADR 0003).
- Exemples : après création, GET du connecteur renvoie `secret: "défini"` sans valeur ; la sentinelle « SENTINELLE-123 » n'apparaît dans aucun journal.
- Questions : algorithme et bibliothèque à vérifier dans la documentation avant choix.

## US9.3 Exécution isolée et sortie réseau contrôlée
- En tant qu'admin de la plateforme, je veux qu'un connecteur compromis ne puisse ni atteindre les magasins de données ni joindre un hôte non prévu.
- Prérequis : US9.1, infra réseau segmentée (`compose.secure.yaml`).
- Acceptance criteria : conteneur dédié non root, système de fichiers en lecture seule ; aucun accès réseau aux magasins ; sortie limitée à la liste blanche du connecteur ; adresses privées, loopback et métadonnées cloud refusées après résolution DNS et après redirection ; tests d'intrusion simples automatisés (URL vers 127.0.0.1, 169.254.169.254, redirection 302 vers un hôte interne).
- Exemples : un connecteur configuré pour `docs.exemple.com` qui reçoit une redirection vers `http://neo4j:7474` : refus journalisé.

## US9.4 Premier connecteur : dépôt de fichiers
- En tant que creator, je dépose des fichiers de façon sûre (c'est US3.1 vu côté sécurité).
- Prérequis : US9.1, IAF-20.
- Acceptance criteria : type vérifié par contenu, taille plafonnée, sha256 ; refus des archives imbriquées au-delà d'une profondeur fixée ; fichier de test hostile (type usurpé, archive piégée) refusé.
- Questions : antivirus, et lequel.

## US9.5 Connecteur vers une source externe
- En tant que creator, je synchronise les documents d'une source externe de façon incrémentale.
- Prérequis : US9.2, US9.3 ; choix de la source.
- Acceptance criteria : à préciser après choix de la source ; au minimum : portée en lecture seule, reprise après échec, suppression côté source répercutée selon règle explicite, quota d'appels respecté.
- Statut : non prêt (source à choisir). Questions : SharePoint, Confluence, dépôt Git, base de données, autre ?

## US9.6 Audit, rotation, révocation
- En tant que creator, je révoque ou fais tourner un identifiant immédiatement ; en tant qu'admin, j'audite.
- Prérequis : US9.2.
- Acceptance criteria : révocation effective au prochain appel au plus tard ; chaque lecture, modification, rotation, révocation journalisée (acteur, connecteur, projet, heure) ; journal en ajout seul, non modifiable par un creator ; suppression d'un projet détruit ses connecteurs et secrets.
- Exemples : après révocation, une synchronisation en cours échoue avec « identifiant révoqué ».
