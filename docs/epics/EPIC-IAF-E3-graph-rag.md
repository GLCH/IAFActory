# EPIC IAF-E3 : graph RAG et ontologies

Jira : IAF-3, stories IAF-20 a IAF-24 (US3.1 a US3.5). Statut : user stories redigees (2026-09-26) a partir du besoin exprime ; le metier n'est pas encore explique, chaque hypothese est a confirmer avant de coder.

Objectif : un creator alimente son projet en documents ; le service construit un graphe de connaissances guide par des ontologies et le rend interrogeable par les agents.

Mise a jour 2026-09-26 : un document peut appartenir a plusieurs classes documentaires, chacune avec son ontologie ; les ontologies sont organisees par glossaires metiers ; la reconnaissance de classe est portee par [IAF-E7](EPIC-IAF-E7-classification-documents.md). Decision Fuseki (serveur du projet Jena) et source de verite : [ADR 0002](../adr/0002-jena-fuseki-glossaires-classes.md). Nouvelles stories US3.6 (glossaires, IAF-63) et US3.7 (document multi-classes, IAF-64).

Decisions 2026-09-26 : le glossaire est commun a tous les projets et structure en taxonomie de domaines ; une classe documentaire utilise un ou plusieurs domaines ; documents PDF, PowerPoint et Word, clairs et techniques, sans manuscrit (donc sans OCR). Nouvelles stories : US3.8 (analyse structurelle, IAF-79), US3.9 (classe et domaines, IAF-80), US3.10 (gouvernance du glossaire commun, IAF-81). US3.6 (IAF-63) reformulee.

Questions transverses : volumes ? langue des documents ? qui modifie la taxonomie commune ? **Ajoutee le 2026-09-28** (constatee reellement) : comment reprendre proprement un document interrompu en cours de traitement sans laisser Neo4j (ecritures non transactionnelles sur tout le pipeline) et Postgres (statut) desynchronises - lie a l'absence de file de taches (ADR 0006).

Mise a jour 2026-09-27 : perimetre MVP confirme par l'utilisateur - viewer et creator interrogent le service, qui bascule graphe (document reconnu) ou RAG (document non reconnu), voir US3.5. La chaine d'agents produit envisagee un temps (conception.md section 14) est mise en pause, hors scope pour l'instant ([IAF-E8](EPIC-IAF-E8-besoin-creator.md), [IAF-E15](EPIC-IAF-E15-agent-codeur.md)).

**Implemente et mesure le 2026-09-27/28** (site/, premiere version reelle, voir aussi IAF-E7 US7.4/US7.5) : ecrans creator (deposer, suivre le statut, voir les classes et leur "ontologie apprise") et ecran viewer (US3.5) reellement construits et testes en conditions reelles (Postgres, Neo4j, passerelle LLM, Ollama local). Resultats mesures :
- **.docx, .pdf et .pptx analyses reellement** (US3.1/US3.8) - pas seulement acceptes. Qualite tres inegale : .docx et .pptx distinguent vraiment titres/sections, paragraphes, tableaux ; .pdf est grossier (pdfplumber ne donne aucun signal de titre fiable : chaque page devient une section, chaque ligne un paragraphe, sans reconstruction de paragraphe). PDF scanne (sans texte) refuse comme prevu, sans OCR.
- **Bug reel corrige** : l'identifiant des elements structurels etait un compteur de process ("se1", "se2"...) valable seulement pour un script jetable (le PoC) ; dans un serveur long-vivant contre un Neo4j persistant, deux documents ingeres par le meme process entrent en collision. Remplace par un uuid4 (site/app/struct_element.py).
- **Reconnaissance a un seul signal insuffisante, mesure reelle** : avec le seuil provisoire 0.75 (cosinus, embedding moyen du document, nomic-embed-text via Ollama local), 5 documents de test topiquement differents (joint torique, roulement a billes, capteur de pression, un document sans rapport sur la validation d'ontologies) ont TOUS ete rattaches a la MEME classe, l'un avec un score de 0.993. Confirme sans ambiguite que ce signal unique ne discrimine pas a ce seuil avec ce modele d'embedding - cite en entree directe pour la calibration US7.7, pas juste une hypothese theorique.
- **Fiabilite de l'extraction** : sur les documents testes, plusieurs chunks ont echoue (delai depasse a 90s, JSON malforme) sans jamais invalider le document (US3.4) - comportement verifie pour de vrai, pas suppose.
- **Limite reelle rencontree, pas encore corrigee** : pas de file de taches asynchrone (ADR 0006 non fait) - un document de 91 chunks met largement plus d'une heure en traitement synchrone bloquant sur ce mate riel (CPU local, sans GPU). Un arret du serveur en cours de traitement laisse Neo4j et Postgres desynchronises (Neo4j n'est pas transactionnel sur tout le pipeline, Postgres ne commite qu'a la fin) - constate reellement avec `user_stories_validation_v3.docx`, resté a moitie ingere. Nouvelle question ouverte ci-dessous.
- Route "Relancer" ajoutee pour reprendre un document reste bloque `received` ou en `error` a partir du fichier deja stocke sur le volume (sha256), sans redepot.

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

## US3.14 Voir les parametres et l'ontologie d'une classe (creator)
- Ajoutee le 2026-09-28, suite a la mesure reelle IAF-E7 (reconnaissance non fiable a un seul signal).
- En tant que creator, je veux voir, pour chaque classe documentaire, son ontologie structurelle (US3.11), son ontologie semantique (US3.12, vocabulaire et concepts induits) et ses parametres de reconnaissance, pour comprendre et ajuster pourquoi un document est reconnu ou non.
- Prerequis : US3.9, US3.11, US3.12, IAF-E7 US7.4 (score de reconnaissance).
- Acceptance criteria : l'ecran classe (deja ebauche dans le site, creator_class_detail.html) affiche : le profil structurel attendu (US3.11), la liste des concepts semantiques (mot -> classe OWL induite, US7.5) avec leurs occurrences, le seuil de reconnaissance en vigueur (90% par defaut, IAF-E7 US7.4) et le score de rattachement de chaque document ; le creator ne peut PAS modifier le seuil global depuis cet ecran dans cette premiere version (lecture seule ; modification = nouvelle story si le besoin se confirme) ; aucune notion de "projet" : les parametres sont par CLASSE, pas par projet (confirme par l'utilisateur le 2026-09-28).
- Contexte : "variables associees aux projets" dans la demande initiale de l'utilisateur, reformule car aucune notion de projet n'existe dans le site (IAF-E3, mis a jour 2026-09-27).
- Exemples : la classe "Fiche technique - Joint torique" affiche 12 concepts semantiques induits (Materiau, Norme, Fournisseur...) et 5 documents rattaches avec un score entre 0.91 et 0.97.
- Questions : le creator doit-il pouvoir ajuster le seuil par classe (pas seulement le lire) dans une version ulterieure ?

**Implementees et mesurees le 2026-09-28 (US3.15, US3.16, US3.17)** : les trois testees reellement de bout en bout via le site (pas seulement le code). US3.15 : nouveaux parseurs markdown_struct.py et latex_struct.py (regex simples, un bug reel corrige pendant le test - une ligne de separation de tableau Markdown comptee deux fois, supprimant a tort la premiere ligne de donnees) ; deux fichiers de test (.md et .tex) deposes ensemble via /creator/documents/bulk, tous deux traites avec succes (3 chunks chacun). US3.16 : ecran document (chunks, telechargement - fichier .docx restitue identique, verifie via `file`), export OWL/XML verifie (RDF/XML bien forme, ouvre un vrai owl:Class par concept). US3.17 : bug reel trouve et corrige pendant le test - `DELETE WHERE` (forme SPARQL raccourcie) ne supporte pas `FILTER`, erreur 500 reelle a la sauvegarde d'une traduction ; corrige avec la forme complete `DELETE {} WHERE {}` ; sauvegarde definition/traduction verifiee dans Fuseki ET Neo4j apres correction (via le vrai formulaire du navigateur, pas seulement en direct).

## US3.15 Deposer un repertoire complet, LaTeX et Markdown
- Ajoutee le 2026-09-28.
- En tant que creator, je depose plusieurs documents d'un coup (un repertoire entier) et des documents source (`.tex`, `.md`), pas seulement des documents bureautiques.
- Prerequis : US3.1, IAF-E13 (mise en file asynchrone, sinon N documents bloquent la page N fois plus longtemps).
- Acceptance criteria : selection d'un repertoire (tous les fichiers de formats acceptes a l'interieur, recursif ou non a preciser a l'usage) ou de plusieurs fichiers ; chaque fichier devient un document independant, mis en file (IAF-E13) ; `.tex` et `.md` analyses structurellement (sections/sous-sections ou titres Markdown -> Section, paragraphes -> Paragraph, tableaux -> Table) selon les memes limites honnetes que les formats existants (analyse simplifiee, pas un moteur LaTeX complet) ; formats non reconnus dans le lot signales individuellement, ne bloquent pas les autres.
- Exemples : un repertoire de 15 fichiers `.md` et 3 `.tex` produit 18 documents en file, chacun avec son propre statut.
- Questions : taille maximale d'un lot ?

## US3.16 Detail d'un document : telecharger, chunks, graphe, export OWL
- Ajoutee le 2026-09-28.
- En tant que creator, je veux, pour un document donne, le telecharger tel que depose, voir ses chunks, visualiser le graphe de connaissance qui en est issu (entites et relations), et acceder a l'ontologie de sa classe au format OWL/XML.
- Prerequis : US3.1 (fichier conserve sur volume), IAF-E7 US7.5 (extraction par chunk), US3.14 (ontologie de classe).
- Acceptance criteria : ecran document dedie (lien depuis la liste des documents) ; telechargement du fichier original (contenu et nom d'origine, pas le nom sha256 interne) ; liste des chunks avec leur texte et leur position structurelle ; visualisation du graphe (noeuds = entites du document, arcs = relations) sans bibliotheque JS externe (coherent avec le site actuel, aucune etape de build) ; export de l'ontologie semantique de la classe du document au format RDF/XML (`Content-Type: application/rdf+xml`), recuperee depuis Fuseki (IAF-E7 US7.5), pas regeneree.
- Exemples : le document "verin_pneumatique_test.docx" telecharge redonne le `.docx` original ; l'export OWL/XML de sa classe s'ouvre dans Protege.
- Questions : la visualisation du graphe doit-elle rester une simple liste si le nombre d'entites est grand (lisibilite) ?

## US3.17 Corpus documentaires et taxonomie des concepts
- Ajoutee le 2026-09-28.
- En tant que creator, je veux une vue d'ensemble des corpus (classes et leurs documents) et de la taxonomie des concepts semantiques (traduction, definition), independamment d'un document ou d'une classe en particulier.
- Prerequis : US3.14, IAF-E7 US7.5.
- Acceptance criteria : ecran "corpus" = liste des classes avec leur nombre de documents (deja couvert par US3.9, re-presente ici comme entree du menu "corpus") ; ecran "taxonomie" = liste de tous les concepts semantiques induits (toutes classes confondues), chacun avec une definition et une traduction editables par le creator (l'induction LLM ne produit ni l'une ni l'autre - champs vides tant que non renseignes, jamais inventes) ; les modifications sont ecrites dans Fuseki (`rdfs:comment` pour la definition, `rdfs:label@en` pour la traduction) et miroitees dans Neo4j.
- Contexte : "traductions et definition" demandes explicitement par l'utilisateur, non couverts par l'induction automatique (IAF-E7 US7.5) qui ne produit qu'un libelle en francais.
- Exemples : le concept "Materiau" (induit sur 3 classes differentes) recoit la definition "Substance dont un composant est fabrique" et la traduction anglaise "Material".
- Questions : une langue de traduction seulement (anglais) ou plusieurs ? gouvernance de ces definitions/traductions (creator seul ou revue collective comme le glossaire, US3.10) ?
- **Precision 2026-09-28** : cette story couvre le "vocabulaire" brut par classe (edition libre, definition/traduction). La construction d'une vraie taxonomie SKOS par agregation de corpus choisis est une nouvelle story distincte, US3.18 ci-dessous.

## US3.18 Construire une taxonomie SKOS par agregation de corpus
- Ajoutee le 2026-09-28.
- En tant que creator, je choisis un ou plusieurs corpus (classes) et je declenche un algorithme de categorisation semantique qui en tire une taxonomie SKOS, regroupant les concepts proches (ex. "Composant" et "Composant mecanique", deja vus dans des donnees reelles) sous un meme concept avec ses variantes.
- Prerequis : IAF-E7 US7.5 (concepts induits par classe), US3.17.
- Acceptance criteria : formulaire de selection (au moins 1 corpus) ; algorithme = regroupement par similarite d'embeddings des libelles de concepts (seuil a calibrer, pas d'arbitrage LLM dans cette premiere version - **decide le 2026-09-28** : liste PLATE, pas de hierarchie broader/narrower) ; chaque groupe devient un `skos:Concept` avec un `skos:prefLabel` (le libelle le plus frequent du groupe) et des `skos:altLabel` pour les variantes ; la taxonomie entiere est un `skos:ConceptScheme`, ecrite dans un graphe nomme dedie et propre dans Fuseki (URI stable, cf. US3.16 pour le principe d'export) ; exportable (`.ttl`, SKOS/Turtle) par telechargement direct ; liste des taxonomies deja construites, avec leurs corpus sources.
- Contexte : "les corpus doivent etre exportables en skos [...] et utilises dans les ontologies lorsqu'on les recherche" (demande explicite). L'usage de la taxonomie construite pour ameliorer le score semantique de reconnaissance (US7.4) - reconnaitre "Composant" et "Composant mecanique" comme le meme concept lors du calcul de recouvrement - est une amelioration separee, pas incluse dans cette premiere version (a faire si le besoin se confirme).
- Exemples : corpus "Joint torique" + "Verin pneumatique" choisis -> taxonomie de 15 concepts uniques (regroupes depuis 22 libelles bruts) exportee en SKOS/Turtle.
- Questions : le seuil de regroupement embeddings est-il fiable sans arbitrage LLM (a mesurer, meme prudence que US7.7) ? faut-il permettre de fusionner manuellement deux concepts que l'algorithme n'a pas regroupes ?
- **Implemente et teste le 2026-09-28** : 3 corpus choisis -> taxonomie reelle de 10 concepts, exportee en SKOS/Turtle valide (skos:ConceptScheme, skos:Concept, skos:prefLabel, skos:inScheme - verifie par lecture directe du fichier telecharge). Aucun regroupement observe sur ce jeu reel (chaque concept reste seul, `alt_labels` = lui-meme) : le seuil 0.85 n'a fusionne aucune paire dans ce test precis - reste a mesurer sur un jeu plus large avant de conclure sur la fiabilite (question ci-dessus toujours ouverte).
