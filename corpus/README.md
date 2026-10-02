# Jeu d'essai rejouable : documents, classes et ontologies

Tout ce qu'il faut pour rejouer l'apprentissage de classes (EPIC-IAF-E17) et la reconnaissance
(EPIC-IAF-E7) sur une instance propre : les documents generes, les classes de depart avec leurs
ontologies semantique et structurelles, des scenarios, et les etats de reference observes.

## Contenu

| Chemin | Contenu |
|---|---|
| `classes.yaml` | classes de depart (Vin, C2SIM) : 1 ontologie semantique + 1..n structurelles chacune |
| `../ontologies/semantic-examples/` | ontologies semantiques : `wine.rdf` (W3C), `c2sim-aggregated.rdf` (TBox pur), `legalruleml-aggregated.rdf` + sources |
| `../ontologies/structure/` | ontologies structurelles : `base`, `livre`, `word`, `pdf`, `latex` (cardinalites OWL2) |
| `documents/vin/` | 10 recits Word + 10 brochures PDF (Gemini, ancres sur 53 vrais vins de l'ontologie) + 3 documents de demonstration |
| `documents/c2sim/` | 2 documents Word generes a partir des definitions reelles de C2SIM |
| `documents/inconnus/` | domaines SANS classe : `astro/` (5), `cuisine/` (4), `droit/` (1), rediges par Gemini |
| `documents/demo-generateur/` | sorties de `doc_generator.py` (sans LLM) avec `manifest.json` (verite terrain) ; les `class_id` du manifest viennent d'instances disparues |
| `scenarios/*.yaml` | scenarios rejouables (ci-dessous) |
| `references/classes/*.ttl` | export Turtle des ontologies de classe observees le 2026-10-02 (Vin, C2SIM, astronomie apprise, droit provisoire) |

## Scenarios

| Scenario | Ce qu'il rejoue |
|---|---|
| `apprentissage-classe-inconnue` | astro x5 + droit x1 : classes provisoires, fusion par densite, promotion, reconnaissance dans la classe promue |
| `cuisine-contre-vin` | 4 recettes reconnues comme Vin au seuil 0,50 (cas limite, base d'une future porte semantique) |
| `reconnaissance-vin` | 20 documents sur une classe connue |
| `reconnaissance-c2sim` | 2 documents sur C2SIM (jamais mesure) |

## Rejouer

Prerequis : infra demarree (Postgres, Neo4j, Fuseki, passerelle LLM), image du site reconstruite
(`docker compose build site && docker compose up -d site`), un compte creator ou admin.
Depuis `site/`, avec le venv du site :

```bash
export REPLAY_EMAIL=... REPLAY_PASSWORD=...        # compte du site, jamais committe
python scripts/replay_learning.py ../corpus/scenarios/apprentissage-classe-inconnue.yaml --dry-run
python scripts/replay_learning.py ../corpus/scenarios/apprentissage-classe-inconnue.yaml --seed --report ../corpus/references/replay-$(date +%F).json
```

- `--seed` cree les classes de depart absentes (`create_class.py` : ontologies structurelles et
  semantique dans Fuseki, miroir Neo4j).
- Les documents passent par la vraie route `POST /creator/documents/new`, un par un, puis le script
  attend le worker et le cycle de vie et affiche chaque decision (reconnaissance, corpus proches,
  similarite vs seuil de densite, fusion, promotion).
- Un document deja depose (meme sha256) est refuse par le site (409) : pour rejouer sur une
  instance qui a deja appris ces classes, `--reset-learned --yes` supprime d'abord les classes non
  seedees et les documents du scenario (routes de suppression du site, cascade complete).

Regenerer des documents inconnus neufs : `python scripts/generate_unknown_domain_documents.py --out <dossier>`.

## Limites a connaitre

- **Rejeu complet valide le 2026-10-02** (`references/replay-2026-10-02.json`) : fusion des deux premiers
  documents d'astronomie, promotion a 3 documents, astro-4 et astro-5 reconnus dans la classe promue.
  Mais le document de droit a ete absorbe par la classe astronomie a la reevaluation : la formule
  combinee (structure Word identique, semantique faible) franchit 0,50, comme pour les recettes
  reconnues comme Vin. Meme cause, meme remede envisage (porte semantique minimale), non implemente.

- Les resultats ne sont pas deterministes : extraction de vocabulaire, entites et redaction des
  documents passent par Gemini. Les notes des scenarios et `references/` sont des observations
  du 2026-10-02, pas des valeurs attendues.
- Les parametres du cycle de vie (plancher de fusion 0,30, k = 1, promotion a 3 documents, seuil de
  reconnaissance 0,50) ne sont pas calibres (US7.7) ; le scenario d'apprentissage a ete enregistre
  en partie avec un plancher de 0,50, un rejeu actuel fusionnera plus tot.
- `references/classes/vin.ttl` contient 81 concepts : les 74 de l'ontologie W3C plus 7 induits par
  deux documents deposes ; `create_class.py` n'en recree que 74.
- LegalRuleML est fourni mais non chargeable (RDFS simple, pas OWL), voir `classes.yaml`.
- Aucune classe de test n'a atteint le pre-filtre structurel (« aucune restriction ») : la fusion
  d'ontologies structurelles n'est verifiee que par test unitaire.
