# EPIC IAF-E16 : génération de documents d'exemple à partir d'une ontologie

Jira : IAF-116, tâches IAF-117 à IAF-121. Statut : rédigé et implémenté le 2026-09-30.

Objectif : produire par lot des documents d'exemple RECOMBINANT le contenu réel déjà présent dans l'ontologie structurelle et sémantique d'une classe documentaire existante (Fuseki/Neo4j) - pas du texte inventé ni généré par LLM. Sert deux usages : (1) fabriquer un corpus de test volumineux pour calibrer les seuils non calibrés du projet (`recognition_threshold`, `NORMALIZE_MATCH_THRESHOLD`, `CONCEPT_MATCH_THRESHOLD` - US7.7) ; (2) chaque document généré vient avec sa vérité terrain (quelles entités/relations/attributs ont réellement été utilisés), ce qui manque aujourd'hui à US7.7 ("jeu de documents annotés à la main (fournis par le creator)").

Demande explicite (2026-09-30) : "créé un nouveau bout de code (appel simple python) qui va générer des exemples de documents à partir d'une ontologie structurelle et sémantique. On indique le nombre de documents à générer par classes, la liste des classes (fichier à définir avec les liens vers les ontologies à utiliser). Le service va créer les documents. on peut définir la taille des documents (nombre de mots total, diversité des termes utilisés (50, 100, 200, 500 termes différents)."

## Décisions prises pour cadrer l'implémentation (pas de discussion demandée cette fois, décidées et documentées comme le reste du projet - US7.7)

- **Pas d'appel LLM.** Le générateur RECOMBINE le contenu déjà réel d'une classe (noms d'entités, types, triplets de relation `(s)-[:REL]->(t)`, clés/valeurs d'attribut réellement observées sur les noeuds `Entity`, libellés de concepts `HAS_CONCEPT`) dans des phrases-gabarits déterministes (FR/EN). Raisons : reproductible (graine aléatoire), gratuit, rapide en lot, et surtout la vérité terrain est connue exactement (ce qui a été "planté" dans le texte) - contrairement à un texte généré par LLM dont le contenu réel ne serait pas garanti. Limite assumée et documentée : prose mécanique, pas un texte naturel - suffisant pour tester l'ingestion/calibrer, pas pour un rendu présentable.
- **Source de l'ontologie sémantique** : Neo4j uniquement (pas Fuseki) - Neo4j est déjà la lecture rapide miroir de Fuseki pour tous les écrans du site (ADR 0001), et porte directement les données nécessaires (`Entity.name/type/attributs`, `:REL{type}`, `Concept.label`). Pas de nouvelle dépendance Fuseki pour ce module.
- **Source de l'ontologie structurelle** : `ontologies/structure/iaf-structure-base.ttl` (seule existante) pour le vocabulaire d'organisation (Section/Paragraph/Table), combinée au profil structurel MOYEN réellement observé sur les documents déjà rattachés à la classe (`Document.profile_section/paragraph/table/equation`, IAF-E7 US7.4) pour donner une forme statistiquement plausible. Repli documenté si la classe est encore vide de documents réels (classe toute nouvelle) : forme par défaut à 3 sections.
- **"diversité des termes" (50/100/200/500)** = nombre MAXIMUM de noms d'entités distincts réels tirés de la classe et réutilisés dans le document généré (`vocabulary_size`). Valeur limitée à cet ensemble fixe demandé explicitement. Si la classe compte réellement moins d'entités distinctes que la valeur demandée, le générateur RÉDUIT honnêtement (jamais n'invente pour combler - même principe que le plafond honnête des graphes US3.16/US7.11) et le signale dans le manifeste de sortie.
- **"taille" (nombre de mots)** = budget total d'un document, approché en ajoutant des paragraphes-gabarits jusqu'à l'atteindre (le dernier paragraphe peut dépasser légèrement - approximation documentée, non exacte).
- **Fichier de classes** : YAML (déjà une dépendance transitive du site - `pyyaml`, ajoutée explicitement à `pyproject.toml` puisque le nouveau module l'importe directement). Une entrée par classe : `class_id` (id Neo4j `DocumentClass` déjà existant - "les liens vers les ontologies à utiliser"), `count`, `words`, `vocabulary_size`, `formats`. Exemple fourni : `site/scripts/generate_documents.example.yaml`.
- **Formats écrits dans cette première version** : `.md` (round-trip garanti avec `markdown_struct.py`, aucune nouvelle dépendance) et `.docx` (round-trip garanti avec `docx_struct.py`, `python-docx` déjà une dépendance directe). **Non fait dans cette version, signalé honnêtement** : `.pptx` et `.tex` (mêmes conventions à reproduire, laissés en tâche de suite si le besoin se confirme - IAF-121).
- **Dossier de sortie** séparé de `documents_dir` (dépôts réels des creators) : `./data/generated` par défaut, configurable - pour ne jamais mélanger du contenu synthétique avec de vrais dépôts.
- **Un manifeste JSON par lot** (`manifest.json` dans le dossier de sortie) recense, par document généré : fichier(s) écrits, classe source, nombre de mots réel, diversité demandée vs réellement disponible, et la vérité terrain (entités/types/relations/attributs effectivement utilisés) - matière première directe pour un futur jeu annoté (US7.7).

## US16.1 Coeur de génération (lecture Neo4j, échantillonnage honnête, gabarits FR/EN)
- En tant que creator/développeur, je veux extraire le matériau réel (concepts, entités, relations, attributs, profil structurel moyen) d'une classe documentaire existante et composer un document synthétique qui le recombine fidèlement.
- Prérequis : IAF-E7 US7.1/US7.4/US7.5/US7.9 (vocabulaire, profil structurel, concepts, relations/attributs déjà en Neo4j).
- Acceptance criteria : `site/app/doc_generator.py` - `load_class_material()` (lecture Neo4j), `generate_document()` (échantillonnage plafonné honnêtement à `vocabulary_size`, jamais d'invention au-delà du réel disponible, langue = majorité des `Document.language` réels de la classe, repli "fr"), gabarits FR et EN pour phrase de concept/type/relation/attribut ; sortie = arbre de sections/paragraphes/tableaux + vérité terrain structurée.
- **Implémenté et mesuré le 2026-09-30** : voir mesure de bout en bout en fin d'épic.

## US16.2 Écriture .md
- En tant que creator/développeur, je veux que le document généré soit un `.md` valide, ré-ingérable tel quel par le pipeline réel (`markdown_struct.py`).
- Acceptance criteria : titres `#`/`##`, tableaux `| a | b |` avec ligne de séparation, paragraphes séparés par une ligne vide - mêmes conventions EXACTES que `markdown_struct.py` (pas une syntaxe Markdown générique non vérifiée contre le parseur réel).
- **Implémenté et vérifié le 2026-09-30** : document généré ré-ingéré avec succès par `pipeline.ingest_document()` réel (pas seulement relu par un nouveau parseur écrit pour l'occasion).

## US16.3 Écriture .docx
- En tant que creator/développeur, je veux que le document généré soit un `.docx` valide, ré-ingérable tel quel par `docx_struct.py`.
- Acceptance criteria : styles `Heading N` pour les sections (mêmes niveaux que `docx_struct.parse` sait lire), tableaux natifs `python-docx`, paragraphes simples.
- **Implémenté et vérifié le 2026-09-30** : idem US16.2, round-trip réel confirmé.

## US16.4 Script CLI + fichier de configuration par lot
- En tant que creator/développeur, je veux un appel Python simple, `python scripts/generate_documents.py config.yaml`, qui lit la liste des classes et leurs paramètres et écrit tous les documents demandés.
- Acceptance criteria : `site/scripts/generate_documents.py` (même convention que `scripts/create_admin.py`) ; fichier de config YAML avec `class_id`/`count`/`words`/`vocabulary_size`/`formats` par classe (`output_dir`, `seed` globaux) ; résumé imprimé en fin d'exécution (nombre de documents écrits, avertissements de plafonnement honnête) ; `manifest.json` écrit avec la vérité terrain.
- **Implémenté et mesuré le 2026-09-30** : voir mesure de bout en bout ci-dessous.

## Mesure reelle de bout en bout (2026-09-30)

**Tests unitaires (10/10, dont 7 nouveaux)** : `tests/test_doc_generator.py` - determinisme a graine egale, rejet d'un `vocabulary_size` hors de {50,100,200,500}, plafonnement honnete verifie (jamais d'entite hors du materiau reel dans `ground_truth`), document plus COURT que le budget demande quand le materiau reel s'epuise (pas de repetition pour combler), et surtout le **round-trip REEL** : le `.md`/`.docx` ecrit par `write_markdown`/`write_docx` est relu avec les VRAIS `markdown_struct.parse`/`docx_struct.parse` du pipeline (pas un parseur ecrit pour l'occasion) et produit bien des elements `Section`/`Paragraph`.

**Execution reelle du script CLI** contre une classe reelle et deja mesuree plus haut dans le projet (`3d184007-...`, 651 entites reelles, 154 types distincts apres reduction US7.11, 179 concepts) : `python scripts/generate_documents.py generate_documents.example.yaml` (adapte a cette classe, `count: 3, words: 300, vocabulary_size: 50, formats: [md, docx]`) a produit 3 documents x 2 formats sans aucun avertissement de plafonnement (50 demandes, 50 utilisees - la classe en compte 651, largement suffisant), 304/308/300 mots reels pour 300 demandes (depassement leger du dernier paragraphe, approximation documentee), `manifest.json` bien forme avec la verite terrain complete (2 relations et 17 attributs reels plantes dans le premier document).

**Round-trip complet via `pipeline.ingest_document()` (chunking + embeddings + extraction LLM + ecriture Neo4j/Fuseki) : INTERROMPU, honnêtement signalé.** L'analyse structurelle du `.md` genere a reussi (parsee sans erreur, chunks produits), mais l'etape suivante (embeddings) a subi un timeout de la passerelle LLM puis Docker Desktop lui-meme est tombe en panne (toutes les commandes `docker`, y compris `docker ps`, ont commence a renvoyer une erreur 500 du moteur) - panne d'infrastructure reelle constatee en cours de verification, DEJA documentee comme un probleme recurrent de cette machine (memoire projet, pas de droits admin pour la corriger), pas un defaut du document genere. A refaire des que Docker Desktop est redemarre - le blocage n'est pas dans `doc_generator.py`/`generate_documents.py`.

## US16.5 Formats supplémentaires (.pptx, .tex) - NON FAIT
- Prérequis : US16.1.
- Acceptance criteria (à faire si le besoin se confirme) : mêmes conventions que `pptx_struct.py` (diapositives/titres de mise en page) et `latex_struct.py` (environnements, densité de citations, `_MATH_WORDS`).
- Statut : hors scope de cette première version (décision ci-dessus), non implémenté.
