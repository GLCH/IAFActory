# EPIC IAF-E7 : classification et reconnaissance des documents

Jira : IAF-37, stories IAF-41 a IAF-47 (US7.1 a US7.7). Statut : rédigé le 2026-09-26. Conception : [conception.md](../design/conception.md) section 4.

Objectif : savoir si un document appartient à des classes documentaires connues (une ou plusieurs, chacune avec son ontologie). Sinon, l'ingérer, créer sa classe et l'utiliser. Aucun seuil ni algorithme n'est acquis avant mesure hors échantillon (US7.7).

Questions transverses : classes partagées entre projets ? langues ? volumétrie (documents, classes) ?

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

## US7.3 Mapper le schéma du document vers les ontologies candidates
- En tant que système, je veux apparier les éléments de `Sd` aux éléments d'ontologie de chaque classe candidate.
- Prérequis : US7.2, US3.3 (ontologies), US3.6 (glossaires).
- Acceptance criteria : correspondances `(e, o, score)` par appariement lexical (labels, synonymes du glossaire) et embeddings ; seuil d'appariement calibré hors échantillon ; les correspondances ambiguës (zone grise) sont marquées ; chaque correspondance est explicable (label, synonyme, distance).
- Contexte : systèmes de référence : LogMap, AML, BERTMap (article AAAI), LLMs4OM. Choix final par US7.7.
- Exemples : « Échéance » dans `Sd` s'apparie à `dateFin` d'une ontologie via le synonyme du glossaire ; « Partie » ne s'apparie à rien : élément non couvert.
- Questions : degré d'usage d'un LLM en arbitrage (coût).

## US7.4 Décider la reconnaissance, y compris multi-classes
- En tant que creator, je veux qu'un document soit rattaché à toutes les classes qui l'expliquent.
- Prérequis : US7.3.
- Acceptance criteria : couverture pondérée et typicité calculées ; sélection gloutonne des classes sur la couverture résiduelle ; relation `IN_CLASS` avec score, couverture, méthode, version des seuils ; explication consultable ; seuils calibrés sur un jeu annoté, validés sur un jeu retenu jamais utilisé pour régler.
- Contexte : formules dans conception.md 4.2.
- Exemples : une annexe technique de contrat couvre 55 pour cent en « contrat » et 40 pour cent en « fiche technique » : deux classes ; un document couvert à 30 pour cent au total : non reconnu.
- Questions : un document peut-il avoir plus de trois classes ?

## US7.5 Créer automatiquement la classe d'un document non reconnu
- En tant que système, je veux ingérer un document inconnu et créer sa classe pour l'utiliser tout de suite.
- Prérequis : US7.4, IAF-22.
- Acceptance criteria : schéma normalisé (fusion de synonymes) publié comme ontologie induite dans un graphe nommé Fuseki ; classe `provisoire` ; termes non reconnus enregistrés comme termes candidats propres au projet (le glossaire commun n'est jamais modifié automatiquement) ; avant création, comparaison aux classes provisoires existantes et rattachement si proche (anti-prolifération) ; le document est utilisable par les agents dès la création selon leur spécialité.
- Contexte : risque principal : explosion de classes quasi identiques. Mesurer le nombre de classes créées sur un lot de documents homogènes.
- Exemples : 20 factures d'un même fournisseur donnent 1 classe, pas 20.
- Questions : nommage automatique de la classe.

## US7.6 Revoir les classes provisoires
- En tant que creator, je valide, renomme, fusionne ou rejette les classes provisoires.
- Prérequis : US7.5, IAF-E5 US5.3.
- Acceptance criteria : liste des classes provisoires avec exemples et schéma ; fusion réaffecte les documents ; rejet déclenche le reclassement des documents concernés ; actions journalisées ; seul le creator propriétaire (et l'admin) peut agir.
- Exemples : fusion de « Facture » et « Facture fournisseur » : les documents des deux sont dans la classe résultante.

## US7.7 Banc d'évaluation des algorithmes de comparaison
- En tant qu'équipe, je veux comparer objectivement les algorithmes avant de fixer le choix.
- Prérequis : jeu de documents annotés à la main (fournis par le creator), US7.2 à US7.4 sous forme de stratégies interchangeables.
- Acceptance criteria : jeu scindé en travail et retenu ; métriques par stratégie : précision, rappel, F1 de l'affectation de classes, rappel@k du pré-filtre, latence, coût ; comparaison au moins de : Jaccard/MinHash, embeddings, lexical+embeddings, avec et sans arbitrage LLM ; rapport publié dans cet epic ; aucune stratégie annoncée meilleure sans résultat sur le jeu retenu.
- Contexte : règle du projet : bénéfices validés hors échantillon.
- Exemples : le rapport indique F1 travail 0.92 et retenu 0.81 pour la stratégie B : c'est le chiffre retenu qui compte.
- Questions : qui annote, combien de documents (ordre de grandeur : une centaine par classe pour commencer, à confirmer).
