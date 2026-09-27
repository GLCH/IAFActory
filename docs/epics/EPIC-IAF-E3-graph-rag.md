# EPIC IAF-E3 : graph RAG et ontologies

Jira : IAF-3, stories IAF-20 a IAF-24 (US3.1 a US3.5). Statut : user stories redigees (2026-09-26) a partir du besoin exprime ; le metier n'est pas encore explique, chaque hypothese est a confirmer avant de coder.

Objectif : un creator alimente son projet en documents ; le service construit un graphe de connaissances guide par des ontologies et le rend interrogeable par les agents.

Mise a jour 2026-09-26 : un document peut appartenir a plusieurs classes documentaires, chacune avec son ontologie ; les ontologies sont organisees par glossaires metiers ; la reconnaissance de classe est portee par [IAF-E7](EPIC-IAF-E7-classification-documents.md). Decision Fuseki (serveur du projet Jena) et source de verite : [ADR 0002](../adr/0002-jena-fuseki-glossaires-classes.md). Nouvelles stories US3.6 (glossaires, IAF-63) et US3.7 (document multi-classes, IAF-64).

Decisions 2026-09-26 : le glossaire est commun a tous les projets et structure en taxonomie de domaines ; une classe documentaire utilise un ou plusieurs domaines ; documents PDF, PowerPoint et Word, clairs et techniques, sans manuscrit (donc sans OCR). Nouvelles stories : US3.8 (analyse structurelle, IAF-79), US3.9 (classe et domaines, IAF-80), US3.10 (gouvernance du glossaire commun, IAF-81). US3.6 (IAF-63) reformulee.

Questions transverses : volumes ? langue des documents ? qui modifie la taxonomie commune ?

Mise a jour 2026-09-27 : perimetre MVP confirme par l'utilisateur - viewer et creator interrogent le service, qui bascule graphe (document reconnu) ou RAG (document non reconnu), voir US3.5. La chaine d'agents produit envisagee un temps (conception.md section 14) est mise en pause, hors scope pour l'instant ([IAF-E8](EPIC-IAF-E8-besoin-creator.md), [IAF-E15](EPIC-IAF-E15-agent-codeur.md)).

## US3.1 Deposer un document
- En tant que creator, je depose un document dans mon projet pour qu'il alimente le graph RAG.
- Prerequis : IAF-1 T4 ; identite creator (IAF-E5 US5.2).
- Acceptance criteria : formats acceptes : PDF, PowerPoint (`.pptx`) et Word (`.docx`) uniquement, refus explicite des autres (dont `.doc` et `.ppt` anciens : conversion demandee) ; un PDF sans couche de texte (scan) est detecte et refuse avec un message clair, aucun OCR ; taille max configurable ; document conserve sur volume avec empreinte sha256 ; un meme contenu redepose dans le meme projet n'est pas duplique ; statut visible (recu, ingere, en erreur).
- Contexte : pas de stockage objet (ADR 0001) : abstraction de stockage a prevoir.
- Exemples : 1) un PDF de 2 Mo est recu, statut "recu". 2) le meme PDF redepose : message "deja present", aucun doublon.
- Exemples complementaires : un `.pptx` de 30 diapositives est accepte ; un scan PDF sans texte est refuse (document scanne : texte non extractible) ; un `.doc` est refuse (convertir en .docx).
- Questions : taille maximale realiste des fichiers ?

## US3.2 Ingerer et decouper en chunks
- En tant que creator, je veux que mon document soit decoupe et vectorise pour etre retrouve par similarite.
- Prerequis : US3.1 ; choix du modele d'embedding (IAF-1 T7).
- Acceptance criteria : noeuds Document et Chunk relies dans Neo4j avec provenance (document, page, position) ; index vectoriel Neo4j cree avec la dimension du modele retenu ; reingestion idempotente ; l'echec d'un chunk n'invalide pas le document.
- Contexte : la dimension de l'index depend du modele ; a mesurer : temps par page et cout.
- Exemples : un document de 40 pages produit N chunks tous relies a leur Document ; relancer l'ingestion ne cree pas de doublon.
- Questions : strategie de decoupage (taille, recouvrement, structure) a comparer sur un jeu de questions.

## US3.3 Charger une ontologie semantique
- En tant que creator, je charge une ontologie semantique (contenu : types d'entites, de relations, attributs ; Turtle, RDF/XML, OWL) pour guider l'extraction. Les ontologies structurelles font l'objet de US3.11.
- Prerequis : IAF-1 T5 (Fuseki) et T4 (n10s) ; decision de source de verite (ADR 0001).
- Acceptance criteria : l'ontologie est stockee dans un graphe nomme Fuseki propre au projet ; un fichier invalide est refuse avec la ligne en cause ; les classes et proprietes sont importees dans Neo4j via n10s ; recharger cree une nouvelle version sans perdre l'ancienne.
- Contexte : `n10s.graphconfig.init()` reste a appeler ; le mode de graphe depend du modele retenu.
- Exemples : 1) une ontologie Turtle de 30 classes est chargee, la requete SPARQL `SELECT ?c WHERE {?c a owl:Class}` renvoie 30. 2) un Turtle tronque est refuse.
- Questions : qui fournit les ontologies ? import de vocabulaires standards (SKOS, schema.org) ?

## US3.4 Extraire entites et relations selon l'ontologie
- En tant que creator, je veux que les chunks deviennent des entites et relations conformes aux ontologies des classes de leur document.
- Prerequis : US3.2, US3.3, US3.7 (classes du document, produites par IAF-E7) ; fournisseur LLM (IAF-E4). Pour un document a plusieurs classes, l'extraction est guidee par l'union de leurs ontologies et chaque entite garde sa classe d'origine.
- Acceptance criteria : chaque entite porte une classe de l'ontologie et un lien vers ses chunks sources ; toute relation hors ontologie est rejetee et comptee ; taux de rejet reporte par document ; resolution des doublons d'entites documentee.
- Contexte : a mesurer sur un jeu annote a la main, hors echantillon (precision, rappel) ; aucun benefice annonce sans cette mesure.
- Exemples : "Marie dirige Acme" avec l'ontologie {Personne, Organisation, dirige} donne (Personne)-[dirige]->(Organisation) ; "Marie aime Acme" est rejete si `aime` n'existe pas.
- Questions : proposer des extensions d'ontologie plutot que rejeter ?

## US3.5 Interroger le graph RAG
- En tant qu'agent (via MCP), je pose une question et recois des passages et des faits du graphe avec leur provenance.
- Prerequis : US3.2, US3.4 ; IAF-E4 US4.4.
- Acceptance criteria : recherche vectorielle puis expansion par voisinage dans le graphe, profondeur configurable ; chaque resultat cite document et position ; isolation stricte au projet (aucun resultat d'un autre projet) ; latence mesuree et reportee ; **confirme le 2026-09-27** - la strategie depend de la reconnaissance du document (US7.4) : document reconnu (rattache a une classe non provisoire au-dessus du seuil) -> expansion par le graphe (entites et relations extraites selon son ontologie) en plus de la recherche vectorielle ; document non reconnu (aucune classe retenue, ou classe provisoire sans extraction fiable) -> recherche vectorielle seule sur les chunks (RAG classique), sans expansion graphe ; le viewer ne voit pas cette distinction technique, seulement le resultat et sa provenance.
- Exemples : une question sur un contrat reconnu renvoie le chunk et l'entite Organisation reliee, avec la page source (voie graphe) ; une question sur un document non reconnu renvoie les chunks les plus proches par similarite, sans entite associee (voie RAG).
- Questions : classement, seuils, nombre de sauts par defaut ; seuil exact de reconnaissance qui declenche la bascule graphe/RAG, a calibrer avec US7.7 (meme regle : pas de seuil affirme sans mesure hors echantillon).

## US3.6 Glossaire commun structure en taxonomie de domaines
- En tant que creator, je m'appuie sur un glossaire metier commun a tous les projets, organise en taxonomie de domaines, plutot que d'en refaire un par projet.
- Prerequis : IAF-11 (Fuseki), US3.3.
- Acceptance criteria : le glossaire est unique et commun (aucun glossaire par projet) ; les domaines forment un arbre (SKOS `broader`/`narrower`) sans cycle, refuse a l'import sinon ; chaque terme (label, synonymes, definition) est rattache a au moins un domaine ; un domaine porte des elements d'ontologie ; import CSV ou Turtle ; terme sans definition signale ; chaque modification cree une version, les classes pointent vers une version ; lecture ouverte a tous les roles.
- Contexte : les synonymes du glossaire alimentent l'appariement lexical (US7.3) ; la hierarchie sert au pre-filtrage par profil de domaines (conception 4). Ecriture : voir US3.10.
- Exemples : domaine « Contrats » avec le sous-domaine « Contrats > Resiliation » ; terme « Echeance » (synonymes « date de fin », « terme ») dans « Contrats » et relie a `dateFin`.
- Questions : glossaire ou taxonomies existants a reprendre (quel format) ? profondeur maximale de l'arbre ?

## US3.7 Rattacher un document a plusieurs classes documentaires
- En tant que creator, je veux qu'un document appartienne a une ou plusieurs classes, chacune avec son ontologie.
- Prerequis : US3.2, US3.3 ; affectations produites par IAF-E7 US7.4.
- Acceptance criteria : relation `IN_CLASS` document vers classe avec score, couverture, methode, version des seuils ; un document peut avoir 0 (avant classification), 1 ou plusieurs classes ; requete « documents de la classe C » et « classes du document D » ; changer une affectation est journalise et recalcule l'extraction guidee (US3.4).
- Exemples : une annexe technique de contrat est dans « Contrat » (0.55) et « Fiche technique » (0.40) ; retirer « Fiche technique » retire les entites qui en dependent.

## US3.8 Analyse structurelle des documents PDF, PowerPoint, Word
- En tant que systeme, je transforme un document technique en contenu structure (titres, paragraphes, tableaux, diapositives) pour un decoupage fidele.
- Prerequis : US3.1.
- Acceptance criteria : squelette structurel (arbre d'elements types : section, diapositive, tableau, figure, liste, legende, note) produit pour chaque document, exprime avec le vocabulaire de `ontologies/structure/iaf-structure-base.ttl` ; titres, tableaux et ordre de lecture conserves pour les 3 formats ; decoupage en chunks selon la structure (section, diapositive, tableau), pas seulement par taille ; chaque chunk garde sa provenance (page ou diapositive, section) ; l'analyse tourne dans un conteneur isole sans acces reseau ; un fichier qui echoue a l'analyse passe en statut « en erreur » avec la cause ; qualite mesuree sur un jeu de documents reels du creator (tableaux correctement restitues, ordre de lecture).
- Contexte : outil candidat Docling (open source, PDF, DOCX et PPTX, OCR optionnel et desactivable, d'apres sa documentation) ; licence, empreinte et qualite sur vos documents a verifier avant adoption. Les documents sont non fiables : analyse isolee.
- Exemples : un tableau de specifications de 12 lignes est restitue en 12 lignes rattachees a leur section ; une diapositive avec titre et 3 puces donne un chunk avec son numero de diapositive.
- Questions : documents avec schemas ou images techniques (legendes seulement, ou description par modele a vision) ?

## US3.9 Classe documentaire utilisant un ou plusieurs domaines
- Complement 2026-09-26 : la classe a aussi un profil structurel (US3.11) ; sa partie semantique vient des domaines ci-dessous.
- En tant que creator, je veux qu'une classe documentaire utilise un ou plusieurs domaines de la taxonomie.
- Prerequis : US3.6, US3.7.
- Acceptance criteria : une classe a au moins un domaine (sauf classe provisoire, voir US7.5) ; relation `USES_DOMAIN` ; l'ontologie de la classe est l'union des ontologies de ses domaines et de leurs descendants ; ajouter ou retirer un domaine recalcule l'ontologie de la classe et declenche le reclassement des documents concernes ; requete « classes utilisant le domaine D (sous-arbre compris) ».
- Exemples : la classe « Fiche technique » utilise « Materiaux » et « Normes » ; la classe « Contrat » utilise « Contrats » : son ontologie inclut aussi « Contrats > Resiliation ».
- Questions : un domaine peut-il etre marque « principal » pour ponderer la classification ?

## US3.10 Gouverner le glossaire commun
- En tant qu'organisation, je veux que la taxonomie commune ne soit pas alteree par erreur ou par un seul projet.
- Prerequis : US3.6, IAF-E5.
- Acceptance criteria : les termes candidats induits par un document non reconnu restent propres au projet ; proposer la promotion d'un terme candidat (ou une modification de la taxonomie : nouveau domaine, renommage) cree une demande visible de tous les creators ; **gouvernance par revue collective des creators (decide le 2026-09-27)** : la demande est approuvee par au moins un creator autre que le proposant (un creator ne peut pas auto-approuver sa propre proposition) ; aucun role de curateur, l'admin ne vote pas ; l'admin garde un droit de recours pour annuler une promotion deja faite si elle s'avere erronee (journalise, motive) ; toute modification est versionnee, journalisee et reversible ; un terme deja utilise par des classes ne peut etre supprime sans reaffectation.
- Contexte : meme mecanisme applique aux ontologies semantiques rattachees aux domaines (US3.12), puisqu'elles sont elles aussi communes. Matrice complete : conception.md section 9.
- Exemples : un creator propose « Joint torique » sous « Materiaux » ; tant que aucun autre creator n'a approuve, le terme reste candidat dans son projet ; le proposant lui-meme ne peut pas approuver sa propre proposition.
- Questions : seuil d'approbation (une seule suffit par defaut, a ajuster si le nombre de creators grandit) ; delai avant relance si personne ne revoit la demande ?

## US3.11 Ontologie structurelle et profil structurel de classe
- Jira : IAF-92.
- En tant que creator, je veux que l'organisation attendue d'une classe de documents soit decrite par une ontologie structurelle, distincte de son contenu.
- Prerequis : US3.8 (squelette), IAF-11 (Fuseki).
- Acceptance criteria : le vocabulaire structurel de base (`ontologies/structure/iaf-structure-base.ttl`) est valide par un parseur RDF (CI) puis charge dans un graphe nomme Fuseki versionne ; l'ontologie structurelle d'une classe specialise ce vocabulaire (roles de sections, imbrications attendues, elements obligatoires ou facultatifs) ; un profil structurel resume le squelette type de la classe et sert a la reconnaissance (US7.8) ; relations `HAS_STRUCTURAL_ONTOLOGY` et `HAS_STRUCTURE_PROFILE` ; modification versionnee, les classes pointent vers une version.
- Contexte : ADR 0005. Les profils structurels sont propres a chaque classe (hypothese) ; le vocabulaire de base est commun.
- Exemples : la classe « Fiche technique » exige un Titre, un Tableau au role « caracteristiques » et accepte une Section « Normes » ; un profil sans element obligatoire est signale.
- Questions : profils partageables entre classes ? profondeur d'imbrication maximale ?

## US3.12 Ontologie semantique par domaine
- Jira : IAF-93.
- En tant que creator, je veux que le contenu attendu d'un domaine soit decrit par une ontologie semantique (types d'entites, de relations, attributs).
- Prerequis : US3.3, US3.6.
- Acceptance criteria : une ontologie semantique est rattachee a un ou plusieurs domaines de la taxonomie (`HAS_ONTOLOGY`) ; elle ne contient aucun terme purement structurel (section, tableau : ils relevent de l'ontologie structurelle) ; controle a l'import ; les elements portent des labels alignes sur les termes du glossaire ; versionnee ; modification gouvernee par la meme revue collective de creators que le glossaire (US3.10, decide 2026-09-27), car rattachee a un domaine commun.
- Contexte : separation structure et contenu, ADR 0005.
- Exemples : le domaine « Materiaux » a les types Materiau, Norme, Fournisseur, la relation « conforme a » et l'attribut « resistance (MPa) » ; un type « Tableau » y est refuse.

## US3.13 Conserver les metadonnees des documents
- Jira : IAF-94.
- En tant que creator, je veux que les metadonnees portees par un document soient conservees et exploitables.
- Prerequis : US3.1, US3.8.
- Acceptance criteria : champs normalises quand presents (titre, auteur, dernier modificateur, sujet, mots-cles, dates de creation et de modification, revision, application productrice, nom du gabarit, langue ; noms des mises en page pour PowerPoint) ; valeur brute et source conservees ; un champ absent reste vide, jamais invente ; metadonnees soumises aux memes droits d'acces que le document (exclusions viewer) ; auteur et dernier modificateur exclus des prompts LLM par defaut ; signal faible de reconnaissance (jamais decisif seul) ; suppression ou anonymisation avec le document ou selon la retention (US12.4).
- Contexte : ADR 0005. Les noms de champs par format et leur accessibilite via l'outil d'analyse retenu sont a verifier. Les metadonnees sont non fiables : traitees comme donnees.
- Exemples : un `.docx` avec titre et auteur : conserves ; un PDF sans propriete : aucune valeur ; un auteur renseigne ne fait pas reconnaitre une classe a lui seul.
- Questions : quelles metadonnees le creator veut-il exploiter ? l'auteur peut-il apparaitre dans une reponse d'agent ?
