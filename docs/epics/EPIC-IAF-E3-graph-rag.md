# EPIC IAF-E3 : graph RAG et ontologies

Jira : IAF-3, stories IAF-20 a IAF-24 (US3.1 a US3.5). Statut : user stories redigees (2026-09-26) a partir du besoin exprime ; le metier n'est pas encore explique, chaque hypothese est a confirmer avant de coder.

Objectif : un creator alimente son projet en documents ; le service construit un graphe de connaissances guide par des ontologies et le rend interrogeable par les agents.

Mise a jour 2026-09-26 : un document peut appartenir a plusieurs classes documentaires, chacune avec son ontologie ; les ontologies sont organisees par glossaires metiers ; la reconnaissance de classe est portee par [IAF-E7](EPIC-IAF-E7-classification-documents.md). Decision Fuseki (serveur du projet Jena) et source de verite : [ADR 0002](../adr/0002-jena-fuseki-glossaires-classes.md). Nouvelles stories US3.6 (glossaires, IAF-63) et US3.7 (document multi-classes, IAF-64).

Questions transverses : formats de documents ? glossaires et ontologies partages entre projets ou propres au projet ? volumes ? langue des documents ?

## US3.1 Deposer un document
- En tant que creator, je depose un document dans mon projet pour qu'il alimente le graph RAG.
- Prerequis : IAF-1 T4 ; identite creator (IAF-E5 US5.2).
- Acceptance criteria : formats acceptes listes et refus explicite des autres ; taille max configurable ; document conserve sur volume avec empreinte sha256 ; un meme contenu redepose dans le meme projet n'est pas duplique ; statut visible (recu, ingere, en erreur).
- Contexte : pas de stockage objet (ADR 0001) : abstraction de stockage a prevoir.
- Exemples : 1) un PDF de 2 Mo est recu, statut "recu". 2) le meme PDF redepose : message "deja present", aucun doublon.
- Questions : formats reels (PDF, docx, html, md) ? OCR ?

## US3.2 Ingerer et decouper en chunks
- En tant que creator, je veux que mon document soit decoupe et vectorise pour etre retrouve par similarite.
- Prerequis : US3.1 ; choix du modele d'embedding (IAF-1 T7).
- Acceptance criteria : noeuds Document et Chunk relies dans Neo4j avec provenance (document, page, position) ; index vectoriel Neo4j cree avec la dimension du modele retenu ; reingestion idempotente ; l'echec d'un chunk n'invalide pas le document.
- Contexte : la dimension de l'index depend du modele ; a mesurer : temps par page et cout.
- Exemples : un document de 40 pages produit N chunks tous relies a leur Document ; relancer l'ingestion ne cree pas de doublon.
- Questions : strategie de decoupage (taille, recouvrement, structure) a comparer sur un jeu de questions.

## US3.3 Charger une ontologie
- En tant que creator, je charge une ontologie (Turtle, RDF/XML, OWL) pour guider l'extraction.
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
- Acceptance criteria : recherche vectorielle puis expansion par voisinage dans le graphe, profondeur configurable ; chaque resultat cite document et position ; isolation stricte au projet (aucun resultat d'un autre projet) ; latence mesuree et reportee.
- Exemples : une question sur un contrat renvoie le chunk et l'entite Organisation reliee, avec la page source.
- Questions : classement, seuils, nombre de sauts par defaut.

## US3.6 Organiser les ontologies par glossaires metiers
- En tant que creator, je regroupe mes ontologies sous des glossaires metiers (contrats, finance, technique...) pour les retrouver et les faire evoluer par domaine.
- Prerequis : IAF-11 (Fuseki), US3.3.
- Acceptance criteria : un glossaire est un ensemble de termes SKOS (label, synonymes, definition) stocke dans un graphe nomme Fuseki ; une ontologie est rattachee a un glossaire ; les termes pointent vers des elements d'ontologie ; import CSV ou Turtle des termes ; un terme sans definition est signale ; chaque modification cree une version.
- Contexte : les synonymes du glossaire alimentent l'appariement lexical de IAF-E7 US7.3. Perimetre (partage entre projets ou par projet) a trancher.
- Exemples : glossaire « Contrats » avec le terme « Echeance » (synonymes : « date de fin », « terme ») relie a la propriete `dateFin` de l'ontologie Contrat.
- Questions : format d'import du creator ? glossaires existants a reprendre ?

## US3.7 Rattacher un document a plusieurs classes documentaires
- En tant que creator, je veux qu'un document appartienne a une ou plusieurs classes, chacune avec son ontologie.
- Prerequis : US3.2, US3.3 ; affectations produites par IAF-E7 US7.4.
- Acceptance criteria : relation `IN_CLASS` document vers classe avec score, couverture, methode, version des seuils ; un document peut avoir 0 (avant classification), 1 ou plusieurs classes ; requete « documents de la classe C » et « classes du document D » ; changer une affectation est journalise et recalcule l'extraction guidee (US3.4).
- Exemples : une annexe technique de contrat est dans « Contrat » (0.55) et « Fiche technique » (0.40) ; retirer « Fiche technique » retire les entites qui en dependent.
