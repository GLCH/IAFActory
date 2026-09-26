# EPIC IAF-E13 : pipeline documentaire

Jira : IAF-83, stories IAF-84 a IAF-91 (US13.1 a US13.8). Statut : rédigé le 2026-09-26. Conception : [conception.md](../design/conception.md) section 13. Décision : [ADR 0006](../adr/0006-orchestration-pipeline-documentaire.md).

Objectif : traiter chaque document par une chaîne de quatre étapes : Ingestion (I), Reconnaissance (R), Création de classe (C, seulement si non reconnu ou partiel), Structuration (S). Cette epic orchestre et rend fiable le travail défini dans IAF-E3 (ingestion, ontologies) et IAF-E7 (reconnaissance). Elle n'a rien à voir avec les pipelines GitHub CI/CD.

Ordre : I, R, (C), S. La création de classe précède la structuration pour un document nouveau, car S a besoin des ontologies que C produit.

Questions transverses : volumétrie (documents par jour) ? priorités entre lots et documents isolés ? orchestration : file Postgres (proposition) ou moteur de workflow ?

## US13.1 Modèle d'exécution du pipeline
- En tant que système, je suis l'état de chaque document dans le pipeline, étape par étape.
- Prérequis : IAF-10 (Postgres), US5.2.
- Acceptance criteria : tables `pipeline_runs` et `stage_runs` (document, étape, statut, empreinte des entrées, version de configuration, durées, erreur, métriques) ; états : reçu, ingéré, reconnu, à créer, classe créée, structuré, en erreur ; transitions autorisées conformes au diagramme de la conception ; une transition interdite est refusée ; chaque transition produit un événement d'activité (US12.1).
- Contexte : option A de l'ADR 0006 (file Postgres) derrière une interface `StageRunner`.
- Exemples : un document `reconnu` ne peut pas passer à `classe créée` ; un document `en erreur` reprend à l'étape échouée.
- Questions : durée de conservation des `stage_runs`.

## US13.2 Étape I : ingestion
- En tant que creator, je dépose un document et il est analysé, découpé et vectorisé.
- Prérequis : US13.1, US3.1 (dépôt), US3.8 (analyse structurelle), US3.13 (métadonnées), passerelle LLM (`iaf-embedding`).
- Acceptance criteria : sortie = Document, métadonnées, squelette `Ss`, chunks par structure, embeddings ; analyse dans un conteneur sans réseau ; échec d'un chunk n'invalide pas le document ; rejeu sans doublon ; scan sans texte, format non supporté ou fichier corrompu : statut `en erreur` avec cause lisible.
- Exemples : un `.docx` de 40 pages produit un squelette de N sections, M tableaux et ses métadonnées (titre, auteur, dates) ; un `.pptx` produit un squelette de diapositives avec leurs noms de mise en page.

## US13.3 Étape R : reconnaissance
- En tant que système, je détermine si le document est reconnu, sur les axes structurel et sémantique.
- Prérequis : US13.2, IAF-E7 (US7.1 à US7.4, US7.8).
- Acceptance criteria : scores structurels et sémantiques par classe candidate ; issue conforme à la matrice de la conception 4.2 (reconnu, variante structurelle, contenu nouveau, classe nouvelle) ; explication consultable ; version des seuils enregistrée ; rejeu identique à configuration égale.
- Exemples : un document dont la forme et le contenu correspondent à « Fiche technique » : issue `reconnu` ; même contenu dans une forme jamais vue : issue `variante structurelle`.
- Questions : seuils, à calibrer hors échantillon (IAF-47).

## US13.4 Étape S : structuration
- En tant que creator, je veux que le contenu soit structuré selon les ontologies des classes du document.
- Prérequis : US13.3 (ou US13.5), US3.11, US3.12, US3.9.
- Acceptance criteria : les éléments structurels (`StructElement`) sont créés selon l'ontologie structurelle de la classe, avec leur rôle quand il est connu ; les entités, relations et attributs sont extraits selon les ontologies sémantiques des classes (union pour un document multi-classes) et rattachés à leurs chunks et à leur élément structurel ; toute relation hors ontologie est rejetée et comptée ; provenance complète (document, élément, position) ; résolution des doublons d'entités documentée ; rejeu sans doublon.
- Contexte : c'est US3.4 replacée dans le pipeline ; mesure de précision et de rappel sur jeu annoté hors échantillon.
- Exemples : « Résistance : 450 MPa » dans le tableau « Caractéristiques » de la fiche donne l'attribut `résistance` sur l'entité Matériau, situé dans cet élément de tableau.

## US13.5 Étape C : création de classe ou de variante
- En tant que système, je crée la classe (ou la variante) quand le document n'est pas entièrement reconnu, puis je relance la structuration.
- Prérequis : US13.3, US7.5.
- Acceptance criteria : selon l'issue : classe provisoire complète (ontologies structurelle et sémantique induites), variante structurelle, ou ontologie sémantique ajoutée ; anti-prolifération appliquée avant création ; le document enchaîne sur S dès la création ; aucune modification automatique du glossaire commun ; tout est réversible par la revue (US7.6).
- Exemples : 20 documents d'un même gabarit inconnu arrivent en lot : une seule classe provisoire, pas vingt.

## US13.6 Reprise, retraitement et versions
- En tant que creator, je veux que mes documents restent cohérents quand une classe ou une ontologie change.
- Prérequis : US13.1.
- Acceptance criteria : reprise d'un document en erreur à l'étape échouée ; modifier ou fusionner une classe, une ontologie ou un seuil déclenche R et S (jamais I) pour les documents concernés, en tâche d'arrière-plan avec progression visible ; les anciens faits d'un document sont remplacés atomiquement (pas d'état mixte visible d'un agent) ; historique des versions de traitement d'un document.
- Exemples : fusion de « Facture » et « Facture fournisseur » : les documents des deux sont restructurés, sans ré-analyser les fichiers.

## US13.7 Suivi du pipeline
- En tant que creator ou admin, je vois où en sont mes documents.
- Prérequis : US13.1, IAF-E12.
- Acceptance criteria : statut par document et par étape, durée, cause d'erreur ; compteurs par étape (en attente, en cours, en erreur) ; taux d'échec, durée médiane et coût LLM par étape au tableau de bord (US12.3) ; le creator ne voit que ses documents ; l'admin voit tout.
- Exemples : le tableau affiche 120 documents structurés, 4 en erreur à l'étape I (scans), 2 à créer.

## US13.8 Lots, concurrence et pause
- En tant qu'admin, je contrôle la charge du pipeline.
- Prérequis : US13.1, passerelle LLM (US11.3).
- Acceptance criteria : concurrence bornée par étape et par projet ; un lot ne bloque pas un document isolé (priorité à définir) ; budget LLM par étape appliqué via la passerelle ; pause et reprise globales par l'admin ; débit réel mesuré et reporté dans ADR 0006 (critère de sortie de l'option A).
- Exemples : dépôt de 500 documents : l'étape R n'exécute jamais plus de N appels simultanés ; la pause suspend les nouvelles étapes sans perdre l'état.
- Questions : valeur de N, règle de priorité.
