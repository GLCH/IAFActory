# EPIC IAF-E18 : sources externes (connecteurs) et documents scannés ou manuscrits

Statut : rédigé le 2026-10-03, demande explicite de l'utilisateur. Mis à jour le 2026-10-03 : Google Drive retiré, OpenRiC en pause (voir la fin du document). Complète [EPIC-IAF-E9](EPIC-IAF-E9-connecteurs-securises.md) (US9.1, US9.2, US9.3 partiel, US9.5, US9.6) et lève la règle « jamais d'OCR » de US3.1 ([pdf_struct.py](../../site/app/pdf_struct.py)).

Objectif : alimenter le pipeline documentaire (I, R, C, S, [EPIC-IAF-E13](EPIC-IAF-E13-pipeline-documentaire.md)) depuis des sources externes et depuis des PDF scannés, y compris manuscrits, puis déterminer ou construire la classe comme pour tout autre document ([EPIC-IAF-E17](EPIC-IAF-E17-classes-inconnues-cycle-de-vie.md)).

Décisions de cadrage (2026-10-03) :
- Le registre de connecteurs applique ce qui est décidé en E9 : états `brouillon`, `en attente`, `approuvé`, `rejeté` ; approbation de l'admin à chaque connecteur ; lecture seule ; identifiants chiffrés en écriture seule ; journal d'audit en ajout seul.
- Version 1 **dans le processus du site** : l'exécution en conteneur isolé (US9.3) n'est PAS faite. La garde réseau est logicielle (liste blanche d'hôtes, https seul, refus des adresses privées, loopback et métadonnées après résolution DNS, redirections revérifiées).
- Un connecteur produit des fichiers qui entrent par le même chemin que le dépôt manuel (`_save_and_enqueue`) : mêmes contrôles (format, taille, sha256, doublons).
- OCR : un modèle de vision (Gemini, via la passerelle LLM) lit les pages rendues en image. Un moteur OCR classique lit mal l'écriture manuscrite ; aucun binaire système n'est ajouté. Conséquence : les images des pages quittent la machine vers le fournisseur du modèle, comme les textes aujourd'hui.

## US18.1 Ontologie RiC-O 1.1 dans le dépôt
- En tant que creator, j'ai l'ontologie principale des archives (Records in Contexts) dans le dépôt.
- Acceptance criteria : `ontologies/semantic-examples/RiC-O_1-1.rdf` présent, provenance et empreinte documentées ; nombre de classes nommées, définitions et limites d'extraction mesurés avec `ontology_import.py`.
- Fait le 2026-10-03 : téléchargé depuis `https://www.ica.org/standards/RiC/RiC-O_1-1.rdf` (validé par l'utilisateur), 1 756 368 octets, sha256 débutant par `885422d9`. Mesure : 16 500 triples ; l'import extrait 109 classes nommées (107 définitions, 113 liens `subClassOf`), 10 individus ; les 486 `owl:ObjectProperty` et 76 `owl:DatatypeProperty` ne sont PAS importés (le module n'importe que les propriétés portées par des individus). Aucune classe n'est créée à partir de ce fichier tant que ce n'est pas demandé.
- Constat : le terme `agentIsTargetOfMandateRelation` de l'ancre de la page n'existe pas dans ce fichier ; le fichier contient notamment `Mandate`, `MandateRelation`, `isAuthorizingAgentInMandateRelation`.

## US18.2 Registre de connecteurs, secrets, audit
- En tant que creator, je déclare un connecteur d'un type du catalogue ; en tant qu'admin, je l'approuve ou le rejette.
- Prérequis : US9.1, US9.2, US9.6.
- Acceptance criteria : catalogue fermé (`openric_api`, `google_drive`) ; table `connectors` (propriétaire, type, nom, configuration non secrète, secret chiffré, état, décision, motif) ; un connecteur non approuvé renvoie 409 à toute synchronisation ; un creator ne voit que ses connecteurs (404 sinon) ; secrets chiffrés par enveloppe (AES-256-GCM, une clé de données par connecteur, chiffrée par une clé maître lue depuis `CONNECTOR_MASTER_KEY` ou `CONNECTOR_MASTER_KEY_FILE`), jamais renvoyés, absents des journaux (test par valeur sentinelle) ; table `connector_events` en ajout seul (création, demande, décision, synchronisation, révocation).
- Exemples : GET d'un connecteur avec secret affiche « défini » sans valeur ; sans clé maître, enregistrer un secret est refusé avec un message explicite.
- Limites assumées : pas de rotation de la clé maître en v1 (un changement de clé rend les secrets illisibles) ; pas de coffre externe.

## US18.3 Garde réseau des connecteurs
- En tant qu'admin, je veux qu'un connecteur ne joigne que ses hôtes prévus.
- Prérequis : US18.2. Reprend le logiciel de US9.3 ; l'isolation conteneur reste à faire.
- Acceptance criteria : https seul ; hôte dans la liste blanche du type ; toute adresse résolue privée, loopback, link-local, réservée ou multicast refusée ; redirections suivies à la main (3 maximum) et revérifiées ; délai et taille de réponse plafonnés.
- Exemples : redirection 302 vers `http://127.0.0.1` refusée ; hôte résolu en `169.254.169.254` refusé.
- Limite assumée : pas d'épinglage de l'adresse résolue, donc une attaque par changement de DNS entre la vérification et la connexion n'est pas exclue.

## US18.4 Connecteur API OpenRiC (RiC-O)
- En tant que creator, je synchronise les notices d'une API OpenRiC (REST, JSON-LD fondé sur RiC-O) comme documents.
- Prérequis : US18.2, US18.3.
- Acceptance criteria : lecture seule ; configuration = URL de base (hôte ajouté à la liste blanche du connecteur) et nombre maximal de notices ; chaque notice (`GET /records`, puis `GET /records/{slug}`) devient un document Markdown (titre, identifiant, niveau, description, étendue, instanciations) ; doublons ignorés par sha256 ; erreurs par notice comptées sans interrompre la synchronisation.
- Vérifié le 2026-10-03 sur l'instance de référence `https://ric.theahg.co.za/api/ric/v1` : `/health` répond `ok`, `/records` renvoie `ric:items`, `/records/{slug}` renvoie le détail JSON-LD ; l'OpenAPI décrit 51 chemins.
- Limites : une seule implémentation de référence d'un éditeur a été trouvée en service ; les relations (`/relations-for/{id}`) et la hiérarchie ne sont pas reprises en v1.

## US18.5 Connecteur Google Drive
- En tant que creator, je connecte mon Google Drive et j'importe les documents d'un dossier.
- Prérequis : US18.2, US18.3 ; un client OAuth 2.0 créé par l'utilisateur dans Google Cloud (identifiant et secret fournis hors conversation, dans `.env` : `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI`).
- Acceptance criteria : flux OAuth 2.0 « application web » (endpoints et paramètres vérifiés dans la documentation Google le 2026-10-03) ; portée `https://www.googleapis.com/auth/drive.readonly` ; `access_type=offline` ; `state` signé et lié au creator et au connecteur ; jeton de rafraîchissement chiffré (US18.2) ; synchronisation d'un dossier (`files.list`, `files.get?alt=media`, `files.export` pour les Google Docs) limitée aux formats acceptés par le dépôt ; consentement donné par l'utilisateur dans son navigateur, jamais saisi par le système.
- Exemples : sans identifiants OAuth configurés, le bouton « Autoriser Google » explique ce qui manque ; un fichier Google Docs est exporté en `.docx`.
- NON vérifié de bout en bout : aucune authentification Google réelle n'a été faite (identifiants à fournir par l'utilisateur) ; le flux est testé avec des réponses simulées.

## US18.6 PDF scannés et manuscrits par OCR
- En tant que creator, je dépose un PDF scanné ou manuscrit ; le système le transcrit puis le classe ou crée une classe.
- Prérequis : US3.1, US13.1, passerelle LLM (ADR 0004).
- Acceptance criteria : une page sans texte ni tableau extractible est rendue en image et transcrite par le modèle de vision (écriture imprimée ou manuscrite, passages douteux marqués `[illisible]`) ; les lignes transcrites deviennent des paragraphes de la page (structure PDF inchangée : page = section) ; plafond de pages OCR par document (`ocr_max_pages`) avec avertissement quand il est atteint ; interrupteur `ocr_enabled` ; trace d'étape « OCR » dans le détail du pipeline ; sans OCR ou si tout échoue, refus comme avant (`ScannedDocument`).
- Exemples : un PDF image seul de 3 pages manuscrites est transcrit, vocabulaire extrait, classe reconnue ou créée ; un PDF de 80 pages scannées est transcrit sur les `ocr_max_pages` premières pages avec avertissement.
- Mesure prévue : test sur un PDF généré avec une police manuscrite (écriture SIMULÉE, pas une vraie main) ; une vraie écriture manuscrite est à fournir par l'utilisateur pour mesurer la fiabilité.
- Risques : coût et latence (un appel de vision par page) ; confidentialité (images envoyées au fournisseur du modèle) ; erreurs de transcription sur écriture difficile, qui se propagent à la classification.

## Mise en oeuvre et mesures (2026-10-03)

Code : `site/app/secret_store.py`, `net_guard.py`, `connectors/` (openric, google_drive), `routers/connectors.py` (creator et admin), tables `connectors` et `connector_events` (migration `a18c0nn3ct01`), `ocr_struct.py` + `pdf_struct.py` + `graph.chat_with_image`, scripts `make_scanned_sample.py` et `measure_ocr.py`, scenario de rejeu `ocr-scanne-manuscrit`. Tests : 97 au total dont 36 connecteurs et 8 OCR (reponses simulees ; Postgres reel pour les routes).

**US18.1** fait (voir plus haut).

**US18.2 / US18.3** : cycle brouillon, en attente, approuve, rejete verifie en reel sur l'instance (creator qui tente d'approuver : 403 ; sync avant approbation : 409 ; page admin 200 ; evenements created, activation_requested, approved, sync). Secrets : jamais affiches ("defini"), sentinelle absente des pages et du journal (test). Cle maitre : `CONNECTOR_MASTER_KEY` dans `.env` (cle locale generee, non committee) ; la variante fichier est geree par `secret_store` mais non branchee comme secret Docker dans `compose.yaml`. Limites : pas de rotation de la cle maitre ; la revocation detruit le secret local mais n'appelle PAS l'endpoint de revocation de Google ; execution dans le processus du site (pas de conteneur isole).

**US18.4 OpenRiC, synchronisation REELLE** sur `https://ric.theahg.co.za/api/ric/v1` : 3 notices importees en 3,0 s. Constat : l'API repond 403 (nginx) au User-Agent par defaut de python-requests et 200 a un User-Agent descriptif ; le garde envoie desormais `IAFActory-connector/1.0 (lecture seule)` (identification honnete, test ajoute). Constat de qualite, NON corrige : ces notices tres courtes (1 a 3 concepts) ont ete rattachees a tort a la classe astronomie (semantique 18 % et 55 %) et a Vin (51 %, un seul concept, « Donnee »), car la mesure semantique est mecaniquement gonflee quand le document a tres peu de concepts (la moitie symetrique cote document vaut 100 % des qu'un concept correspond). Les 3 documents, 2 concepts exclusifs et 6 entites devenues orphelines ont ete retires apres le test. Piste (decision a prendre) : exiger un nombre minimal de concepts pour reconnaitre un document (sinon classe provisoire).

**US18.5 Google Drive** : NON authentifie en reel. Verifie : routes, `state` signe (faux, autre utilisateur, expire refuses), echange de code et synchronisation avec reponses simulees, message explicite quand les variables OAuth manquent (reel : 409 citant `GOOGLE_OAUTH_CLIENT_ID`). A faire par l'utilisateur : creer le client OAuth (type application web), renseigner `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI` (identique au URI autorise), redemarrer le site, puis Autoriser Google depuis la fiche du connecteur. Risque connu : une redirection Google vers un hote hors liste blanche serait refusee.

**US18.6 OCR, mesure REELLE** (Gemini via la passerelle) sur un PDF de 2 pages en police manuscrite (ecriture SIMULEE, pas une vraie main) : similarite moyenne de caracteres 0,998 sur 12 lignes (9 identiques apres normalisation ; les 3 autres different d'un caractere). Chemin complet par la vraie route de depot : 69 s, etape `OCR (US18.6)` tracee (2 pages), pre-filtre structurel (premiere observation reelle : 1 classe candidate, score 100 %), score structure 92 %, semantique 0 %, classe provisoire creee (`porte semantique` 15 %). Non mesure : vraie ecriture manuscrite, PDF mixtes texte et scan en reel, plafond de pages en reel (teste avec modele simule).

## Nombre minimal de concepts pour appartenir a une classe (2026-10-03, demande de l'utilisateur)

Regle : `recognition_min_concepts = 4` (config, affichee dans les parametres). Un document de moins de 4 concepts distincts n'est jamais rattache a une classe existante (methode `concepts_insuffisants_creation_provisoire`, motif distinct de la porte semantique et du score) ; une classe provisoire de moins de 4 concepts n'est pas absorbee dans une officielle ; deux classes provisoires ne se comparent que si elles sont du meme cote du minimum (pauvres avec pauvres, riches avec riches) ; la promotion en classe officielle exige aussi 4 concepts en plus des 3 documents (promotion differee, tracee). La reconnaissance est decidee par la fonction pure `pipeline.recognition_verdict`, la promotion par `class_lifecycle.promotion_decision`.

Calibrage (mesure, non calibre au sens US7.7) : notices parasites 1 et 3 concepts ; documents reels de 300 mots, de 4 (astro-2) a 23 concepts. 4 est juste au-dessus des parasites observes : marge fine, et un vrai document tres pauvre en vocabulaire (4 concepts) reste a la limite.

Verification reelle (synchronisation OpenRiC, 3 notices) : (1) premier essai avec seulement la regle de reconnaissance : les 3 notices restent provisoires, mais elles fusionnent entre elles (meme niveau de preuve) et la classe de 3 documents et 3 concepts est PROMUE officielle : defaut corrige en exigeant aussi le minimum a la promotion ; (2) second essai : 3 notices, une classe provisoire de 3 documents, trace « Promotion differee : 3 document(s) mais 1 concept(s) : minimum 4 ». Les classes et documents de test ont ete supprimes apres chaque essai. 4 tests ajoutes (101 au total). Non rejoue : le scenario d'apprentissage astro complet (un document de 3 concepts ou moins ne serait plus reconnu).

## Décisions du 2026-10-03 (suite) : Google Drive retiré, OpenRiC en pause, accès par les paramètres

- **US18.5 Google Drive : RETIRÉ** (jugé pas assez mature). Supprimés : `app/connectors/google_drive.py`, les routes d'autorisation et de retour OAuth, les variables `GOOGLE_OAUTH_*` (config, compose, `.env.example`), les tests et les textes d'interface. Rien n'avait été authentifié en réel.
- **US18.4 OpenRiC : EN PAUSE** (« comment et quand accéder aux données » reste à définir ; pour l'instant on ne fait rien). Le code est conservé, mais le type est marqué `enabled: False` dans `app/connectors/__init__.py` : aucun connecteur de ce type ne peut être créé, activé (demande ou approbation) ni synchronisé (409). Le catalogue de production est testé tel quel. Pour rouvrir : décision, puis `enabled: True`.
- **Accès** : le lien « Connecteurs » de la barre de navigation du creator est supprimé ; la gestion est dans la page **Paramètres** (carte Connecteurs : liste, état, lien « Gérer »). L'admin garde sa page d'approbation. Les connecteurs **approuvés** sont visibles de tous les rôles sur la page des connaissances ([EPIC-IAF-E19](EPIC-IAF-E19-services-agentises.md)).
- Le registre, les secrets chiffrés, l'audit et la garde réseau restent en place pour les futurs connecteurs. Tests : 105 au total (30 connecteurs, 15 connaissances, 3 architecture).

## Correction de l'OCR sur un document réel : « document regiment cp lalo.pdf » (2026-10-03, signalé par l'utilisateur)

Constat de l'utilisateur : un PDF photocopié d'un vieux document, difficile à lire même pour un humain, était « totalement mal lu » : seuls des chiffres étaient extraits.

**Cause 1 (principale) : l'OCR ne se déclenchait jamais.** Le PDF (36 pages) a été fabriqué dans Word : une photo par page (72 à 79 % de la surface, 1564 x 2255 pixels) et, pour seule couche de texte, le numéro de page (1 à 2 caractères). Mon critère « page sans aucun texte » n'était donc jamais vrai : les 36 numéros de page ont été ingérés (36 « chunks », 19 « entités » issues de chiffres, un concept). Correction : une page est à transcrire quand une image la couvre à 40 % ou plus et que sa couche de texte fait moins de 80 caractères (`needs_ocr`) ; la transcription remplace alors la couche de texte, qui reste seulement en repli si la transcription échoue.

**Cause 2 : rendu trop pauvre.** La page était rendue à 150 dpi, ce qui sous-échantillonnait l'image native. Mesure sur ce document : à 150 dpi pleine page, le modèle lit « STAUFPEN » (au lieu de STAUFFEN), « n décembre » (au lieu de 10 décembre), range des lignes dans le désordre et lit le numéro de page parasite ; au rendu **recadré sur l'image à sa résolution native** (plafond 3000 pixels), il lit correctement ces passages dans le bon ordre. Le rendu natif est donc le défaut.

**Cause 3 : boucle de répétition sur les lignes de séparation.** Les pages 6 et 22 n'ont donné que 4 et 10 lignes alors qu'elles en contiennent une trentaine : le modèle (finish_reason = length) a écrit des milliers de `- - - -` et `=====` (46 000 caractères en page 22) jusqu'à épuiser ses 3 000 jetons avant d'avoir transcrit le reste. Corrections : consigne d'ignorer les lignes de séparation, détection de la troncature (`TruncatedReply`) avec nouvel essai en deux moitiés de page qui se recouvrent, réduction des répétitions de signes, avertissement `ocr_truncated_pages` si une moitié est encore coupée. Mesure : page 6 de 4 à 49 lignes, page 22 de 10 à 73 lignes (tableau compris, lu cellule par cellule).

**Autres changements** : plafond `ocr_max_pages` 20 -> 60 (le document a 36 pages : 16 auraient été ignorées) ; transcriptions en parallèle (`ocr_parallelism` = 4) avec ordre des pages conservé et mémoire bornée ; consigne adaptée aux documents anciens, dactylographiés, photographiés de biais, avec transparence du verso.

**Résultat mesuré sur le document réel (36 pages, vrai modèle)** : 36 pages transcrites en 58 s, 1 918 lignes, 96 000 caractères, aucune page tronquée ; pages courantes entre 40 et 70 lignes. Il s'agit du journal de marche de la 11e compagnie du RMLE (1945) : couverture manuscrite, puis pages dactylographiées. Limites visibles : quelques erreurs de lecture isolées (« muit » pour « nuit », mots coupés ou fusionnés quand une ligne est partiellement masquée par la transparence du verso), 16 lignes composées uniquement de chiffres (numéros de page du document) sur 1 918, ordre de lecture des tableaux cellule par cellule. Aucune vérité terrain complète n'existe pour ce document : la qualité a été jugée en lisant des pages (1, 6, 9, 20, 22) contre leur image, pas par une mesure de caractères.

Tests : 19 sur l'OCR (dont photo avec numéro de page seul, texte de repli si l'OCR échoue, grande image avec vrai texte non transcrite, vignette ignorée, ordre sous parallélisme, résolution native et plafond, troncature et moitiés, nettoyage des séparateurs).

### Suite : paragraphes et retraitement du document (2026-10-03)

**Cause 4 : une ligne imprimée = un chunk.** Le pipeline crée un chunk par élément de texte, et un PDF donne un élément par ligne : la première ré-ingestion correcte du journal (36 pages) produisait 2 003 chunks, des phrases coupées en deux et plus d'une heure d'extraction (constaté à 686 chunks traités après 20 minutes). Un essai de demander au modèle de recoller les lignes n'a pas été fiable (il garde les retours à la ligne et introduit du bruit de transparence). **Correction en code, déterministe** (`ocr_struct.paragraphs`) : une ligne continue le paragraphe sauf titre, date en tête, numéro de page, rangée de tableau (cellules séparées par « | », demandé au modèle), élément de liste, ou fin de paragraphe (ponctuation finale après une ligne nettement plus courte que les lignes pleines de la page, 80e centile) ; un mot coupé par un tiret en fin de ligne est recollé ; plafond de 1 500 caractères par paragraphe, coupé à une fin de phrase. Mesure : page 9 de 50 lignes à 23 paragraphes, page 6 de 49 à 28. 9 tests de paragraphes (extrait réel de la page 9).

**Retraitement du document réel par la vraie route** (ancienne classe de chiffres supprimée, même fichier redéposé) : OCR de 36 pages en 78 s ; **946 chunks** (contre 36 numéros de page au départ et 2 003 par lignes) ; vocabulaire de 160 termes, 36 concepts ; 2 208 entités, 466 types de relation, 174 attributs ; classe provisoire « Provisoire - document regiment cp lalo.pdf » (aucune ressemblance avec les classes existantes : 3 % avec l'astronomie, 2 % avec C2SIM) ; durée totale 36 minutes, dominée par l'extraction d'entités par chunk (un appel de modèle par chunk). Un chunk a échoué à l'extraction (JSON mal formé), signalé en avertissement.

**Ce que le système sait désormais** (questions posées au service d'exposition, portée creator) : réponses ancrées avec sources sur des faits du journal, par exemple le sort du légionnaire Bodnar (tué à 15 H par un éclat, inhumé à Bitscheiller) ou la mission de la section Robillard ; une question hors sujet (recette de tarte) reçoit un aveu d'ignorance sans appel au modèle. Réserves : la réponse sur le Lieutenant Lalo mélange deux fragments et signale elle-même l'ambiguïté avec le Capitaine Lalo ; la réponse sur le 10 décembre à Thann cite un autre passage (« compagnie restée dans ses anciens cantonnements ») car le passage du 10 décembre contient des fautes de lecture ; quelques erreurs de lecture OCR subsistent (« muit », mots fusionnés quand la transparence du verso gêne).

**À savoir (non fait)** : les PDF qui ont une vraie couche de texte sont toujours découpés en un chunk par ligne (même défaut), ce qui modifierait aussi leur profil structurel utilisé pour la reconnaissance : à traiter à part, après mesure sur des classes existantes. L'extraction d'entités par chunk reste le poste le plus long du traitement.

Tests : 134 pour le site (dont 28 sur l'OCR) et 29 pour le service d'exposition.
