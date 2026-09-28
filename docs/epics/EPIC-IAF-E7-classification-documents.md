# EPIC IAF-E7 : classification et reconnaissance des documents

Jira : IAF-37, stories IAF-41 a IAF-47 (US7.1 a US7.7). Statut : rédigé le 2026-09-26. Conception : [conception.md](../design/conception.md) section 4.

Objectif : savoir si un document appartient à des classes documentaires connues (une ou plusieurs, chacune avec son ontologie). Sinon, l'ingérer, créer sa classe et l'utiliser. Aucun seuil ni algorithme n'est acquis avant mesure hors échantillon (US7.7).

Questions transverses : classes partagées entre projets ? langues ? volumétrie (documents, classes) ?

**Mesure reelle 2026-09-27/28** (site/, premiere version simplifiee a un seul signal - embedding moyen du document, cosinus, seuil fixe 0.75, nomic-embed-text via Ollama local) : 5 documents de test topiquement distincts (joint torique, roulement a billes, capteur de pression, document sans rapport sur la validation d'ontologies) ont tous ete rattaches a la meme classe par ce signal seul, un score atteignant 0.993. Confirme reellement (pas juste suppose) que la cascade complete US7.2/US7.3 (MinHash/LSH, domaines, arbitrage LLM) est necessaire avant de pouvoir se fier a la reconnaissance - le signal embedding seul ne suffit pas a ce seuil avec ce modele. Detail : [EPIC-IAF-E3](EPIC-IAF-E3-graph-rag.md).

**Decisions 2026-09-28 (suite a la mesure ci-dessus)** :
- Le site NE GERE PAS de notion de "projet" (mise en pause le 27/09) : uniquement des classes documentaires et leurs ontologies. Une classe a une ontologie STRUCTURELLE (US3.11 - forme attendue : doc d'architecture, doc de securite, fiche produit...) et une ontologie SEMANTIQUE/metier simple (US3.12 - concepts attendus dans le contenu). Confirme par l'utilisateur : "on ne gere que les classes de document et les ontologies qui les decrivent [...] on s'attend a une ontologie" par classe.
- Seuil de reconnaissance a 90%, **a la fois** runtime (US7.4 : score combine requis pour rattacher a une classe existante) et mesure (US7.7 : precision/rappel sur le jeu retenu requis avant qu'une strategie soit utilisee par defaut).
- Le concept (classe OWL) associe a chaque mot du vocabulaire extrait est **induit automatiquement par le LLM** (pas de gouvernance collective a ce stade, contrairement au glossaire commun US3.10) - ecrit dans l'ontologie semantique induite de la classe (US7.5, deja specifie pour Fuseki mais jamais implemente reellement).
- L'extraction de vocabulaire (US7.1) est une **etape dediee**, distincte de l'extraction d'entites/relations par chunk qui existe deja dans le site (site/app/pipeline.py) - pas une extension de cette derniere.

**Implemente et mesure le 2026-09-28** (site/, deuxieme version reelle du pipeline) :
- US7.1 : etape dediee d'extraction de vocabulaire (site/app/pipeline.py, `extract_vocabulary`), un appel LLM sur un extrait du document (borne a 4000 caracteres, cout/latence). Chaque terme associe a un concept par le LLM.
- US7.5 (partiel) : chaque concept induit ecrit comme une vraie classe OWL dans un graphe nomme Fuseki par classe documentaire (site/app/ontology.py), verifie reellement par requete SPARQL directe (7 classes OWL confirmees pour une classe de test). Neo4j reste la lecture rapide pour les ecrans (US3.14).
- US7.4 (partiel, pas la cascade complete) : score combine = moyenne d'un score STRUCTUREL (profil de proportions Section/Paragraph/Table, comparaison L1) et d'un score SEMANTIQUE (recouvrement Jaccard entre le vocabulaire du nouveau document et les concepts deja connus de chaque classe), compare au seuil 0.90. Mesure reelle : un document topiquement distinct ("politique de securite") n'a plus ete rattache a tort a la classe "joint torique" (comme avec l'ancien signal cosinus seul) - il a cree sa propre classe provisoire, resultat attendu.
- US3.14 : ecran classe (creator_class_detail.html) etendu - profil structurel moyen, concepts semantiques avec termes observes, seuil en vigueur, score combine/structurel/semantique par document.
- Toujours PAS fait : US7.2 (MinHash/LSH, pre-filtrage par domaines - chaque document est compare a TOUTES les classes existantes, pas seulement des candidates pre-filtrees), US7.3 (arbitrage LLM sur les correspondances ambigues), US7.7 (banc d'evaluation avec jeu annote et mesure precision/rappel - le seuil 0.90 est un choix runtime, pas une preuve mesuree).
- Gemini (`gemini-vertex-flash`, ADR 0004) remplace Ollama local comme modele par defaut du site depuis le 2026-09-28 : environ 37 secondes par document contre plusieurs minutes avant, moins d'echecs de delai dans les tests reels de ce tour.

## US7.1 Extraire sans a priori le schéma d'un document
- En tant que creator, je veux que chaque document soit analysé sans ontologie imposée, pour connaître ses propres types d'entités et de relations.
- Prérequis : IAF-21 (chunks), fournisseur LLM (IAF-4).
- Acceptance criteria : sortie = triplets libres + schéma `Sd` (types d'entités, relations, poids par fréquence) ; chaque élément renvoie aux chunks sources ; extraction déterministe à graine et paramètres fixés ; coût et durée par document mesurés.
- Contexte : approche de référence EDC (extract, define, canonicalize), qui fonctionne sans schéma cible ([arXiv 2404.03868](https://arxiv.org/abs/2404.03868)). À mesurer : qualité sur documents réels du creator.
- Exemples : un contrat de 20 pages produit `Sd` avec Partie, Obligation, Échéance et relations « oblige », « échoit le » ; deux exécutions identiques donnent le même `Sd`.
- Questions : langue, taille max avant découpage par sections.

## US7.2 Pré-filtrer les classes candidates
- En tant que système, je veux retrouver rapidement les k classes probables d'un document sans comparer avec toutes.
- Prérequis : US7.1, au moins une classe existante.
- Acceptance criteria : trois voies combinées : (a) MinHash + LSH sur l'ensemble des termes de `Sd`, (b) similarité d'embeddings vers les centroïdes de classes (index vectoriel Neo4j), (c) profil de domaines de la taxonomie commune (termes de `Sd` projetés sur les domaines, crédit décroissant aux domaines plus larges) comparé aux domaines de chaque classe ; rappel@k mesuré sur le jeu annoté (la bonne classe est dans les k) ; latence par document mesurée et reportée.
- Contexte : MinHash/LSH approxime le Jaccard sans toutes les paires (documentation datasketch). Le point faible connu est la forme de surface.
- Exemples : un document dont 80 pour cent des termes sont ceux de la classe « contrat » place « contrat » dans le top-3.
- Questions : valeur de k, taille des signatures.

## US7.3 Mapper le schéma du document vers les ontologies sémantiques candidates
- En tant que système, je veux apparier les éléments de `Sd` aux éléments des ontologies sémantiques de chaque classe candidate (l'axe structurel est traité par US7.8).
- Prérequis : US7.2, US3.3 (ontologies), US3.6 (glossaires).
- Acceptance criteria : correspondances `(e, o, score)` par appariement lexical (labels, synonymes du glossaire) et embeddings ; seuil d'appariement calibré hors échantillon ; les correspondances ambiguës (zone grise) sont marquées ; chaque correspondance est explicable (label, synonyme, distance).
- Contexte : systèmes de référence : LogMap, AML, BERTMap (article AAAI), LLMs4OM. Choix final par US7.7.
- Exemples : « Échéance » dans `Sd` s'apparie à `dateFin` d'une ontologie via le synonyme du glossaire ; « Partie » ne s'apparie à rien : élément non couvert.
- Questions : degré d'usage d'un LLM en arbitrage (coût).

## US7.4 Décider la reconnaissance, y compris multi-classes
- En tant que creator, je veux qu'un document soit rattaché à toutes les classes qui l'expliquent.
- Prérequis : US7.3.
- Acceptance criteria : décision croisée structure x sémantique selon la matrice de la conception 4.2 (reconnu, variante structurelle, contenu nouveau, classe nouvelle), **confirmée par l'utilisateur le 2026-09-27** ; une variante structurelle reste dans la même classe sémantique (décidé, ne crée jamais de classe distincte) ; couverture pondérée et typicité calculées ; sélection gloutonne des classes sur la couverture résiduelle ; relation `IN_CLASS` avec score, couverture, méthode, version des seuils ; explication consultable ; seuils calibrés sur un jeu annoté, validés sur un jeu retenu jamais utilisé pour régler ; **décidé le 2026-09-28** - le score combiné (structure x sémantique, pas un seul signal) doit dépasser **90%** pour rattacher a une classe EXISTANTE ; en dessous, US7.5 (classe provisoire). Ce seuil runtime est distinct de l'exigence de qualité mesurée d'US7.7 (les deux sont demandées : un seuil qui décide au moment de l'ingestion, une mesure hors échantillon qui dit si on peut lui faire confiance).
- Contexte : formules dans conception.md 4.2. Mesure réelle du 2026-09-28 (voir tête d'epic) : le signal cosinus seul ne discrimine pas, d'où l'exigence de score COMBINÉ (structurel + sémantique) avant d'atteindre 90%, pas un simple relèvement du seuil sur le même signal unique.
- Exemples : une annexe technique de contrat couvre 55 pour cent en « contrat » et 40 pour cent en « fiche technique » : deux classes ; un document couvert à 30 pour cent au total : non reconnu ; la même fiche technique en PowerPoint (contenu reconnu, forme nouvelle) reste dans la classe « Fiche technique » avec un profil structurel variante.
- Questions : un document peut-il avoir plus de trois classes ?

## US7.5 Créer automatiquement la classe d'un document non reconnu
- En tant que système, je veux ingérer un document inconnu et créer sa classe pour l'utiliser tout de suite.
- Prérequis : US7.4, IAF-22.
- Acceptance criteria : selon l'issue de US7.4 : classe provisoire complète, variante structurelle, ou ontologie sémantique ajoutée ; squelette généralisé en profil structurel induit ; schéma normalisé (fusion de synonymes) publié comme ontologie sémantique induite dans un graphe nommé Fuseki ; classe `provisoire` ; termes non reconnus enregistrés comme termes candidats propres au projet (le glossaire commun n'est jamais modifié automatiquement) ; avant création, comparaison aux classes provisoires existantes et rattachement si proche (anti-prolifération) ; le document est utilisable par les agents dès la création selon leur spécialité.
- Contexte : risque principal : explosion de classes quasi identiques. Mesurer le nombre de classes créées sur un lot de documents homogènes.
- Exemples : 20 factures d'un même fournisseur donnent 1 classe, pas 20.
- Questions : nommage automatique de la classe.

## US7.6 Revoir les classes provisoires
- En tant que creator, je valide, renomme, fusionne ou rejette les classes provisoires.
- Prérequis : US7.5, IAF-E5 US5.3.
- Acceptance criteria : liste des classes provisoires avec exemples et schéma ; fusion réaffecte les documents ; rejet déclenche le reclassement des documents concernés ; actions journalisées ; seul le creator propriétaire (et l'admin) peut agir.
- Exemples : fusion de « Facture » et « Facture fournisseur » : les documents des deux sont dans la classe résultante.
- **Étendue et mesurée le 2026-09-28** : la fusion peut être déclenchée par une mesure de distance ENTRE CLASSES (même score combiné structurel + sémantique que US7.4, appliqué cette fois à deux classes plutôt qu'à un document et une classe), pas seulement à la main. Deux seuils réglables (`/creator/settings`, décidé par l'utilisateur - "un mélange des deux") : au-delà du seuil **auto** ("sans doute"), fusion automatique si l'option est activée ; entre le seuil de **suggestion** et le seuil auto, une proposition de fusion est créée pour le creator (jamais automatique dans cette tranche). La fusion automatique est DÉSACTIVÉE par défaut. Testé réellement de bout en bout : suggestion créée automatiquement après ingestion (score 50%, 100% structurel/0% sémantique sur deux documents peu proches), fusion manuelle validée - documents réaffectés (Postgres), relation IN_CLASS et concepts fusionnés (Neo4j, dédupliqués par libellé), ontologie RDF fusionnée dans Fuseki (`ADD`+`CLEAR`, vérifié : graphe source vide après fusion, graphe cible contient les deux jeux de concepts), ancienne URL de classe redirige (302) vers la classe absorbante plutôt que 404.

**Revision du 2026-09-29** (constate par l'utilisateur : "extraction... reste pauvre", "il y a toujours 0% de matching semantique", "une etape de normalisation... doit etre faite", "il manque comment construire le corpus") :
- **US7.1** : jusqu'a 150 termes (etait 10-20) ; le LLM recoit les concepts deja connus (toutes classes) pour les reutiliser ; langue detectee et respectee (jamais forcee en francais).
- **US7.4, bug reel corrige** : le score semantique (Jaccard sur libelles EXACTS) restait quasiment toujours a 0% en pratique - remplace par une correspondance souple par embedding (`_concept_set_similarity`, seuil 0.80 non calibre). Densite de citations ajoutee comme signal structurel d'appoint (LaTeX).
- **US7.5** : langue detectee utilisee pour le tag RDF des concepts (etait toujours `@fr`, meme pour un document en anglais - bug reel corrige) ; embedding de chaque concept stocke sur le noeud Neo4j.
- **US7.9** : meme normalisation (embedding, seuil 0.85) appliquee aux types d'entites (par classe) et aux relations/attributs (globaux).
- **US7.10 (nouvelle story ci-dessous)** : regroupement en lot des classes proches, premiere reponse a "comment construire le corpus".
- **Mesure reelle** (paire de documents ML francais/anglais, memes theoremes/equations) : score combine 67% (structurel 100%, **semantique 34.8%** - non-zero, confirme le correctif) ; vocabulaire passe de 12 a 22 concepts sur un document comparable. Jira : IAF-41/44/45/110/114.

## US7.10 Construire un corpus par regroupement en lot des classes proches
- Ajoutee le 2026-09-29. Jira : IAF-114.
- En tant que creator, je regroupe en un lot toutes les classes provisoires suffisamment proches (structure + semantique), plutot que d'attendre qu'un nouveau document les rapproche une par une.
- Prerequis : US7.4, US7.6 (IAF-46).
- Acceptance criteria : compare TOUTES les paires de classes actives (pas seulement une classe neuve contre les autres, comme le mecanisme reactif existant) ; cree une suggestion de fusion (meme mecanisme que US7.6) pour chaque paire au-dessus du seuil de suggestion ; PAS de fusion automatique en lot meme si active - la revue explicite reste preferable pour une action qui touche potentiellement beaucoup de classes a la fois.
- Contexte : "a partir de n documents similaires (structure + semantique) on genere un corpus" - reponse PARTIELLE : regroupe des classes DEJA CREEES, ne resout pas la creation initiale groupee de N documents apportes ensemble sans classe prealable (le rattachement/creation reste document par document, pipeline.py inchange).
- Exemples : 5 classes provisoires quasi identiques (meme gabarit deja rencontre plusieurs fois) recoivent chacune une suggestion de fusion vers la plus grande en un seul clic "Regrouper les corpus proches".
- **Implemente et teste le 2026-09-29** : sur 2 classes ML (francais/anglais), la paire a ete correctement detectee et proposee (67%) une fois le seuil de suggestion abaisse pour le test (0.90 par defaut n'aurait pas suffi sur ce cas precis - non calibre, US7.7).

## US7.9 Proprietes OWL partagees (relations, attributs)
- Ajoutee le 2026-09-28, suite au constat direct : l'ontologie ecrite dans Fuseki (US7.5) ne contient que des concepts (owl:Class), jamais les relations extraites (US3.4/US13.4) ni les attributs.
- En tant que creator, je veux que les relations et attributs extraits des documents existent comme de vraies proprietes OWL (object property, datatype property), pas seulement des arcs/proprietes Neo4j.
- Prerequis : US7.5, IAF-E13 US13.4 (extraction par chunk).
- Acceptance criteria : chaque type de relation extrait (ex. "fabrique_par") devient un `owl:ObjectProperty` ; chaque cle d'attribut extraite (ex. "resistance") devient un `owl:DatatypeProperty` ; **decide le 2026-09-28** - portee GLOBALE (un graphe nomme partage, pas un par classe) : une propriete est definie UNE fois et reutilisee par toutes les classes qui l'emploient, coherent avec les pratiques OWL reelles ; ecriture idempotente comme pour les concepts (US7.5).
- Contexte : graphe nomme dedie (`http://iafactory.local/ontology/properties`), distinct des graphes par classe des concepts.
- Exemples : la relation "fabrique_par" vue dans deux classes differentes (joint torique, verin pneumatique) n'a qu'UNE definition owl:ObjectProperty, pas deux.
- Questions : faut-il declarer domain/range (quelles classes utilisent quelle propriete) ? Pas fait dans cette premiere version.
- **Implemente et verifie le 2026-09-28** : relation "fabrique_par" ("fabriqué par") confirmee ecrite comme owl:ObjectProperty dans le graphe nomme partage, verifiee par requete SPARQL directe.

## US7.7 Banc d'évaluation des algorithmes de comparaison
- En tant qu'équipe, je veux comparer objectivement les algorithmes avant de fixer le choix.
- Prérequis : jeu de documents annotés à la main (fournis par le creator), US7.2 à US7.4 sous forme de stratégies interchangeables.
- Acceptance criteria : jeu scindé en travail et retenu ; métriques par stratégie : précision, rappel, F1 de l'affectation de classes, rappel@k du pré-filtre, latence, coût ; comparaison au moins de : Jaccard/MinHash, embeddings, lexical+embeddings, avec et sans arbitrage LLM ; rapport publié dans cet epic ; aucune stratégie annoncée meilleure sans résultat sur le jeu retenu ; **décidé le 2026-09-28** - une stratégie n'est éligible en production que si précision ET rappel, mesurés sur le jeu RETENU (jamais celui de réglage), dépassent **90%** ; en dessous, elle reste en évaluation, jamais le comportement par défaut du site.
- Contexte : règle du projet : bénéfices validés hors échantillon. La première version reelle du site (2026-09-27/28) tournait sur un seul signal (cosinus, non calibré) precisement parce que ce banc n'existe pas encore - mesure reelle qui motive cette story.
- Exemples : le rapport indique F1 travail 0.92 et retenu 0.81 pour la stratégie B : c'est le chiffre retenu qui compte, et 0.81 < 0.90 -> pas encore en production.
- Questions : qui annote, combien de documents (ordre de grandeur : une centaine par classe pour commencer, à confirmer).

## US7.8 Reconnaissance structurelle
- Jira : IAF-95.
- En tant que système, je veux reconnaître la forme d'un document à bas coût, sans LLM, à partir de son squelette et de ses métadonnées.
- Prérequis : US3.8 (squelette), US3.11 (profils structurels), US3.13 (métadonnées).
- Acceptance criteria : score structurel par classe candidate calculé à partir de : similarité des libellés de titres normalisés (Jaccard, accéléré par MinHash), cosinus des histogrammes de types d'éléments, forme de l'imbrication ; métadonnées (gabarit, noms de mises en page) en signal faible qui ne suffit jamais seul ; aucun appel LLM ; résultat déterministe ; explication (éléments qui ont fait correspondre) ; seuil `tau_struct` calibré sur un jeu annoté hors échantillon ; rappel@k et latence mesurés (US7.7).
- Contexte : réduit les appels LLM en écartant tôt les classes de forme incompatible. Point faible attendu : titres renommés ou traduits. Distance d'édition d'arbres : à vérifier avant d'être envisagée.
- Exemples : deux fiches techniques au même gabarit Word obtiennent un score structurel élevé sans appel LLM ; la même fiche exportée en PowerPoint obtient un score faible et une issue « variante structurelle ».
- Questions : poids relatifs des trois mesures, à mesurer.
