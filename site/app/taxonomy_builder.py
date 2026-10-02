"""US3.18/US3.19 : construction d'une taxonomie SKOS HIERARCHIQUE par
agregation de corpus (classes) choisis par le creator.

**Revision du 2026-09-28 (US3.19)** : la version precedente (US3.18, meme
jour) faisait un regroupement glouton a un seuil unique sur les embeddings
des libelles de concepts, en liste PLATE (decide explicitement le 2026-09-28,
"pas de hierarchie broader/narrower"). L'utilisateur redemande une VRAIE
classification hierarchique avec un "algo de NLP standards" : remplace par
une classification ascendante hierarchique (CAH / hierarchical agglomerative
clustering, `scipy.cluster.hierarchy`, liaison moyenne) - c'est la methode
citee dans la litterature NLP pour ce probleme, contrairement au
regroupement glouton a seuil unique qui n'est pas un veritable algorithme de
classification hierarchique. Cette version se limitait a 2 niveaux.

**Revision du 2026-10-02 (taxonomie PROFONDE, demande explicite : "celle sur
le vin a tres peu de profondeur, alors qu'il y a beaucoup de sous concept de
concept. Les regions, terroir, types de vin par exemple peuvent etre
regroupes sur plusieurs niveaux")** : le plafond a 2 niveaux etait un choix
"a etendre si le besoin se confirme". Trois sources de hierarchie, de la plus
fiable a la moins :
1. la hierarchie REELLE de l'ontologie (`(:Concept)-[:SUBCLASS_OF]->(:Concept)`,
   `rdfs:subClassOf` du fichier importe - jamais inventee ; C2SIM : 169
   relations, profondeur 7 ; Wine : seulement 13, les classes y sont surtout
   definies par des restrictions anonymes, mesure reelle) ;
2. pour les concepts SANS hierarchie reelle, une induction par LLM
   (`_group_items_llm`) qui range TOUS les concepts fournis (chacun
   exactement une fois, libelle exact) sous des categories nommees sur
   plusieurs niveaux ; sortie VALIDEE (concepts manquants/inconnus/dupliques)
   et rejetee au profit du repli si elle ne tient pas ;
3. repli sans LLM : classification ascendante hierarchique a seuils croissants
   (`_group_items_clustering`), plusieurs niveaux au lieu de deux.
Les definitions reelles des concepts (rdfs:comment importes, ou saisies) sont
portees sur les noeuds de la taxonomie (`skos:definition`).

Les seuils de repli (`COARSENING_DISTANCES`) restent NON calibres (meme
prudence que le reste du projet, US7.7 : pas de seuil affirme sans mesure
hors echantillon).

Execute de facon SYNCHRONE (pas via worker.py comme l'ingestion) : le nombre
de concepts a regrouper est petit (une poignee a quelques centaines par
corpus, un seul appel LLM pour l'induction). A revoir si des corpus avec des
milliers de concepts rendent cette hypothese fausse (US13.7, meme principe
que "tout process long")."""
from __future__ import annotations

import re
import uuid

from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist

from . import ontology
from .config import settings
from .graph import chat_json, embed, get_driver

HIERARCHY_LABEL_SYSTEM = (
    "Tu recois une liste de concepts d'un domaine technique, deja proches semantiquement. "
    "Propose UN libelle general (un a trois mots, en francais) qui les regroupe tous, sans "
    "inventer un terme hors du champ de ces concepts. Reponds UNIQUEMENT en JSON valide, sans "
    'texte autour, au format exact : {"label":"..."}.'
)

INDUCTION_SYSTEM = (
    "Tu es terminologue. On te donne la liste des concepts d'un domaine (libelles anglais ou francais, "
    "souvent en CamelCase). Organise-les en une taxonomie hierarchique PROFONDE, sur 3 a 5 niveaux : "
    "regroupe-les en categories thematiques naturelles du domaine (pour le vin par exemple : regions et "
    "terroirs, appellations, cepages, types et couleurs de vin, descripteurs sensoriels, etc.), avec des "
    "sous-categories imbriquees. Regles STRICTES : (1) chaque concept fourni apparait EXACTEMENT UNE FOIS, "
    "comme feuille, avec son libelle EXACT (copie-le caractere pour caractere) ; (2) tu inventes uniquement "
    "des libelles de CATEGORIES (en francais, 1 a 4 mots), jamais de nouveau concept feuille ; (3) une "
    "categorie a au moins 2 enfants ; (4) pas plus de 10 enfants directs par categorie quand c'est evitable. "
    'Reponds UNIQUEMENT en JSON valide : {"categories": [{"label": "...", "children": ["ConceptExact", '
    '{"label": "...", "children": []}]}]}.'
)

COARSENING_DISTANCES = (0.15, 0.30, 0.45, 0.60)  # NON calibres


def _humanize(label: str) -> str:
    """"StEmilion" -> "St Emilion", "WineBody" -> "Wine Body" : les libelles
    d'ontologies importees sont en CamelCase, que les modeles d'embedding
    separent mal."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", label)


def _broader_label(members: list[str]) -> str:
    """Repli explicite (pas d'invention silencieuse) : le libelle le plus
    court des membres si l'appel LLM echoue - meme principe que
    extract_vocabulary (pipeline.py) qui renvoie une liste vide plutot que de
    planter."""
    try:
        result = chat_json(
            ", ".join(members), model=settings.extraction_model, system=HIERARCHY_LABEL_SYSTEM, timeout=60,
            max_tokens=2000,  # meme prudence que pipeline.py : reponse courte mais jetons de raisonnement Gemini a couvrir
        )
        label = result.get("label")
        if label:
            return label
    except Exception:
        pass
    return min(members, key=len)


def tree_depth(nodes: list[dict]) -> int:
    return max((1 + tree_depth(n.get("children", [])) for n in nodes), default=0)


def build_taxonomy(name: str, class_ids: list[str]) -> str:
    if not class_ids:
        raise ValueError("aucun corpus choisi")

    driver = get_driver()
    with driver.session() as session:
        rows = list(session.run(
            "MATCH (c:DocumentClass)-[:HAS_CONCEPT]->(concept:Concept) WHERE c.id IN $ids "
            "RETURN DISTINCT concept.label AS label, concept.definition AS definition",
            ids=class_ids,
        ))
        edge_rows = list(session.run(
            "MATCH (a:Concept)-[:SUBCLASS_OF]->(b:Concept) WHERE a.class_id IN $ids AND b.class_id IN $ids "
            "RETURN DISTINCT a.label AS child, b.label AS parent",
            ids=class_ids,
        ))
    labels = sorted({r["label"] for r in rows if r["label"]})
    if not labels:
        raise ValueError("aucun concept dans les corpus choisis")
    definitions: dict[str, str] = {}
    for r in rows:
        if r["label"] and r["definition"] and r["label"] not in definitions:
            definitions[r["label"]] = r["definition"]
    label_set = set(labels)
    edges = [(r["child"], r["parent"]) for r in edge_rows if r["child"] in label_set and r["parent"] in label_set]

    tree = _build_hierarchy(labels, edges, definitions)

    taxonomy_id = str(uuid.uuid4())
    ontology.write_taxonomy(taxonomy_id, name, tree)

    def _count(nodes: list[dict]) -> int:
        return sum(1 + _count(n.get("children", [])) for n in nodes)

    with driver.session() as session:
        session.run(
            "CREATE (t:Taxonomy {id: $id, name: $name, created_at: datetime(), concept_count: $n, depth: $depth})",
            id=taxonomy_id, name=name, n=_count(tree), depth=tree_depth(tree),
        )
        for class_id in class_ids:
            session.run(
                "MATCH (t:Taxonomy {id: $tid}), (c:DocumentClass {id: $cid}) "
                "CREATE (t)-[:FROM_CORPUS]->(c)",
                tid=taxonomy_id, cid=class_id,
            )

        def _write_node(node: dict, parent_neo_id: str | None) -> None:
            concept_neo_id = str(uuid.uuid4())
            session.run(
                "CREATE (c:TaxonomyConcept {id: $id, pref_label: $pref, alt_labels: $alts, definition: $definition})",
                id=concept_neo_id, pref=node["pref_label"], alts=node["alt_labels"],
                definition=node.get("definition"),
            )
            if parent_neo_id is None:
                session.run(
                    "MATCH (t:Taxonomy {id: $tid}), (c:TaxonomyConcept {id: $cid}) "
                    "CREATE (t)-[:HAS_TAXONOMY_CONCEPT]->(c)",
                    tid=taxonomy_id, cid=concept_neo_id,
                )
            else:
                session.run(
                    "MATCH (p:TaxonomyConcept {id: $pid}), (c:TaxonomyConcept {id: $cid}) "
                    "CREATE (p)-[:NARROWER]->(c)",
                    pid=parent_neo_id, cid=concept_neo_id,
                )
            for child in node.get("children", []):
                _write_node(child, concept_neo_id)

        for top in tree:
            _write_node(top, None)

    return taxonomy_id


def _real_forest(labels: list[str], edges: list[tuple[str, str]], definitions: dict[str, str]) -> list[dict]:
    """Foret issue de la hierarchie REELLE de l'ontologie. Un concept a
    plusieurs parents (polyhierarchie, rare : 1 sur la classe Wine) est range
    sous son premier parent par ordre alphabetique - un arbre SKOS ici, pas
    un graphe ; les autres liens ne sont pas perdus dans l'ontologie
    elle-meme. Les cycles eventuels sont ignores."""
    parent_of: dict[str, str] = {}
    for child, parent in sorted(edges):
        if child != parent and child not in parent_of:
            parent_of[child] = parent
    for label in labels:  # coupe les cycles : remonte, retire l'arete qui ferme la boucle
        seen, current = {label}, label
        while current in parent_of:
            nxt = parent_of[current]
            if nxt in seen:
                del parent_of[current]
                break
            seen.add(nxt)
            current = nxt

    children_of: dict[str, list[str]] = {}
    for child, parent in parent_of.items():
        children_of.setdefault(parent, []).append(child)

    def _node(label: str) -> dict:
        node = {
            "pref_label": label, "alt_labels": [label],
            "children": [_node(c) for c in sorted(children_of.get(label, []))],
        }
        if definitions.get(label):
            node["definition"] = definitions[label]
        return node

    return [_node(label) for label in labels if label not in parent_of]


def _build_hierarchy(
    labels: list[str], edges: list[tuple[str, str]] | None = None, definitions: dict[str, str] | None = None,
) -> list[dict]:
    """Arbre PROFOND (2026-10-02) : hierarchie reelle quand elle existe, puis
    regroupement multi-niveaux (LLM valide, sinon CAH) des racines restantes."""
    forest = _real_forest(labels, edges or [], definitions or {})
    if len(forest) <= 3:
        return forest
    try:
        grouped = _group_items_llm(forest, definitions or {})
        if grouped is not None:
            return grouped
    except Exception:
        pass
    return _group_items_clustering(forest)


def _group_items_llm(forest: list[dict], definitions: dict[str, str]) -> list[dict] | None:
    by_label = {n["pref_label"]: n for n in forest}
    listing = []
    for label in by_label:
        entry = label
        hint = definitions.get(label)
        if hint:
            entry += f"  -- {hint[:90]}"
        listing.append(entry)
    result = chat_json(
        "Concepts :\n" + "\n".join(listing), model=settings.extraction_model, system=INDUCTION_SYSTEM,
        timeout=180, max_tokens=12000,
    )
    categories = result.get("categories")
    if not isinstance(categories, list) or not categories:
        return None

    used: set[str] = set()

    def _convert(entry) -> dict | None:
        if isinstance(entry, str):
            label = entry.strip()
            if label in by_label and label not in used:
                used.add(label)
                return by_label[label]
            return None
        if isinstance(entry, dict) and entry.get("label"):
            children = [c for c in (_convert(x) for x in entry.get("children", [])) if c is not None]
            if not children:
                return None
            if len(children) == 1:
                return children[0]  # pas de categorie a un seul enfant
            category = str(entry["label"]).strip()
            return {"pref_label": category, "alt_labels": [category], "children": children}
        return None

    tree = [t for t in (_convert(c) for c in categories) if t is not None]
    missing = [label for label in by_label if label not in used]
    if len(missing) > max(2, len(by_label) // 10):
        return None  # sortie trop incomplete : repli plutot qu'une taxonomie amputee
    tree.extend(by_label[label] for label in missing)  # jamais perdus, restent de tete sans parent invente
    return tree


def _group_items_clustering(forest: list[dict]) -> list[dict]:
    """Repli sans LLM : CAH (liaison moyenne) a seuils croissants appliquee
    plusieurs fois - un groupe de plusieurs elements devient une categorie
    (libelle propose par le LLM, a defaut le libelle le plus court) ; un
    element isole passe tel quel au niveau suivant."""
    items = list(forest)
    if len(items) < 2:
        return items
    vectors = [embed(_humanize(n["pref_label"])) for n in items]
    for distance in COARSENING_DISTANCES:
        if len(items) < 2:
            break
        z = linkage(pdist(vectors, metric="cosine"), method="average")
        cluster_ids = fcluster(z, t=distance, criterion="distance")
        groups: dict[int, list[int]] = {}
        for index, cluster_id in enumerate(cluster_ids):
            groups.setdefault(int(cluster_id), []).append(index)
        if len(groups) == len(items):
            continue
        next_items, next_vectors = [], []
        for members in groups.values():
            if len(members) == 1:
                next_items.append(items[members[0]])
                next_vectors.append(vectors[members[0]])
                continue
            label = _broader_label([items[i]["pref_label"] for i in members])
            next_items.append({"pref_label": label, "alt_labels": [label], "children": [items[i] for i in members]})
            member_vectors = [vectors[i] for i in members]
            next_vectors.append([sum(c) / len(member_vectors) for c in zip(*member_vectors)])
        items, vectors = next_items, next_vectors
    return items
