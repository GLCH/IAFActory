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
classification hierarchique.

Principe (2 niveaux, pas plus dans cette version - a etendre si le besoin de
plus de profondeur se confirme sur des corpus plus larges) :
1. CAH sur les embeddings des libelles bruts -> concepts FINS (niveau
   "narrower"), coupee a `FINE_DISTANCE`.
2. CAH sur les centroides des concepts fins -> groupes plus GENERAUX (niveau
   "broader"), coupee a `COARSE_DISTANCE`.
3. Un groupe general de plusieurs concepts fins recoit un libelle par UN
   appel LLM (le concept fin le plus court en repli si le LLM echoue, jamais
   d'invention silencieuse) ; un concept fin isole (aucun autre concept fin
   ne lui est assez proche) reste un concept de tete SANS parent invente.

Les deux seuils (`FINE_DISTANCE`, `COARSE_DISTANCE`) restent NON calibres
(meme prudence que le reste du projet, US7.7 : pas de seuil affirme sans
mesure hors echantillon) - `FINE_DISTANCE` reprend la valeur de l'ancien
`CLUSTER_THRESHOLD` (0.85 de similarite = 0.15 de distance cosinus) pour
rester comparable a la mesure US3.18 deja faite.

Execute de facon SYNCHRONE (pas via worker.py comme l'ingestion) : le nombre
de concepts a regrouper est petit (une poignee a quelques dizaines par
corpus). A revoir si des corpus avec des centaines de concepts rendent cette
hypothese fausse (US13.7, meme principe que "tout process long")."""
from __future__ import annotations

import uuid

from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist

from . import ontology
from .config import settings
from .graph import chat_json, embed, get_driver

FINE_DISTANCE = 0.15  # 1 - 0.85 (ancien CLUSTER_THRESHOLD de similarite, US3.18)
COARSE_DISTANCE = 0.35  # groupement plus large pour le niveau "broader" - NON calibre

HIERARCHY_LABEL_SYSTEM = (
    "Tu recois une liste de concepts d'un domaine technique, deja proches semantiquement. "
    "Propose UN libelle general (un a trois mots, en francais) qui les regroupe tous, sans "
    "inventer un terme hors du champ de ces concepts. Reponds UNIQUEMENT en JSON valide, sans "
    'texte autour, au format exact : {"label":"..."}.'
)


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


def build_taxonomy(name: str, class_ids: list[str]) -> str:
    if not class_ids:
        raise ValueError("aucun corpus choisi")

    driver = get_driver()
    with driver.session() as session:
        rows = list(session.run(
            "MATCH (c:DocumentClass)-[:HAS_CONCEPT]->(concept:Concept) WHERE c.id IN $ids "
            "RETURN DISTINCT concept.label AS label",
            ids=class_ids,
        ))
    labels = sorted({r["label"] for r in rows if r["label"]})
    if not labels:
        raise ValueError("aucun concept dans les corpus choisis")

    tree = _build_hierarchy(labels)

    taxonomy_id = str(uuid.uuid4())
    ontology.write_taxonomy(taxonomy_id, name, tree)

    def _count(nodes: list[dict]) -> int:
        return sum(1 + _count(n.get("children", [])) for n in nodes)

    with driver.session() as session:
        session.run(
            "CREATE (t:Taxonomy {id: $id, name: $name, created_at: datetime(), concept_count: $n})",
            id=taxonomy_id, name=name, n=_count(tree),
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
                "CREATE (c:TaxonomyConcept {id: $id, pref_label: $pref, alt_labels: $alts})",
                id=concept_neo_id, pref=node["pref_label"], alts=node["alt_labels"],
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


def _build_hierarchy(labels: list[str]) -> list[dict]:
    """Renvoie un arbre a 2 niveaux : liste de concepts de tete, chacun avec
    ses enfants ("children", vide pour un concept isole)."""
    if len(labels) == 1:
        return [{"pref_label": labels[0], "alt_labels": labels, "children": []}]

    vectors = [embed(label) for label in labels]

    # Niveau fin : CAH sur les libelles bruts.
    fine_distance = pdist(vectors, metric="cosine")
    fine_z = linkage(fine_distance, method="average")
    fine_ids = fcluster(fine_z, t=FINE_DISTANCE, criterion="distance")

    fine_groups: dict[int, list[str]] = {}
    for label, fine_id in zip(labels, fine_ids):
        fine_groups.setdefault(int(fine_id), []).append(label)

    vector_by_label = dict(zip(labels, vectors))
    fine_items = []
    for members in fine_groups.values():
        member_vectors = [vector_by_label[m] for m in members]
        centroid = [sum(coords) / len(member_vectors) for coords in zip(*member_vectors)]
        fine_items.append({
            "pref_label": min(members, key=len),
            "alt_labels": members,
            "centroid": centroid,
        })

    if len(fine_items) < 2:
        return [{"pref_label": i["pref_label"], "alt_labels": i["alt_labels"], "children": []} for i in fine_items]

    # Niveau general : CAH sur les centroides des groupes fins.
    centroids = [i["centroid"] for i in fine_items]
    coarse_distance = pdist(centroids, metric="cosine")
    coarse_z = linkage(coarse_distance, method="average")
    coarse_ids = fcluster(coarse_z, t=COARSE_DISTANCE, criterion="distance")

    coarse_groups: dict[int, list[dict]] = {}
    for item, coarse_id in zip(fine_items, coarse_ids):
        coarse_groups.setdefault(int(coarse_id), []).append(item)

    tree = []
    for members in coarse_groups.values():
        if len(members) == 1:
            # Concept isole : aucun regroupement plus general trouve, il
            # reste seul en tete (pas de parent invente pour un concept qui
            # n'en a pas besoin).
            item = members[0]
            tree.append({"pref_label": item["pref_label"], "alt_labels": item["alt_labels"], "children": []})
        else:
            broader = _broader_label([m["pref_label"] for m in members])
            tree.append({
                "pref_label": broader,
                "alt_labels": [broader],
                "children": [
                    {"pref_label": m["pref_label"], "alt_labels": m["alt_labels"], "children": []}
                    for m in members
                ],
            })
    return tree
