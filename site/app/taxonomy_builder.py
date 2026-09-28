"""US3.18 : construction d'une taxonomie SKOS par agregation de corpus (classes)
choisis par le creator. Algorithme de categorisation semantique = regroupement
glouton par similarite d'embeddings des libelles de concepts (pas d'arbitrage
LLM dans cette premiere version, decide le 2026-09-28) ; liste PLATE, pas de
hierarchie skos:broader/narrower.

Execute de facon SYNCHRONE (pas via worker.py comme l'ingestion) : le nombre
de concepts a regrouper est petit (une poignee a quelques dizaines par
corpus), contrairement a l'ingestion d'un document qui multiplie les appels
LLM par chunk. A revoir si des corpus avec des centaines de concepts rendent
cette hypothese fausse (US13.7, meme principe que "tout process long")."""
from __future__ import annotations

import uuid

from . import ontology
from .graph import cosine_similarity, embed, get_driver

# Seuil de regroupement, NON calibre (US3.18 le signale explicitement comme
# question ouverte - meme prudence que le reste du projet : pas de seuil
# affirme sans mesure hors echantillon, US7.7).
CLUSTER_THRESHOLD = 0.85


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

    clusters: list[dict] = []  # {"alt_labels": [...], "embedding": [...]}
    for label in labels:
        vector = embed(label)
        best_cluster, best_score = None, 0.0
        for cluster in clusters:
            score = cosine_similarity(vector, cluster["embedding"])
            if score > best_score:
                best_cluster, best_score = cluster, score
        if best_cluster is not None and best_score >= CLUSTER_THRESHOLD:
            best_cluster["alt_labels"].append(label)
        else:
            clusters.append({"alt_labels": [label], "embedding": vector})

    for cluster in clusters:
        # Libelle prefere = le plus court des variantes (souvent le plus
        # general - heuristique simple, pas d'arbitrage LLM dans cette version).
        cluster["pref_label"] = min(cluster["alt_labels"], key=len)

    taxonomy_id = str(uuid.uuid4())
    ontology.write_taxonomy(
        taxonomy_id, name,
        [{"pref_label": c["pref_label"], "alt_labels": c["alt_labels"]} for c in clusters],
    )

    with driver.session() as session:
        session.run(
            "CREATE (t:Taxonomy {id: $id, name: $name, created_at: datetime(), concept_count: $n})",
            id=taxonomy_id, name=name, n=len(clusters),
        )
        for class_id in class_ids:
            session.run(
                "MATCH (t:Taxonomy {id: $tid}), (c:DocumentClass {id: $cid}) "
                "CREATE (t)-[:FROM_CORPUS]->(c)",
                tid=taxonomy_id, cid=class_id,
            )
        for cluster in clusters:
            session.run(
                "MATCH (t:Taxonomy {id: $tid}) "
                "CREATE (t)-[:HAS_TAXONOMY_CONCEPT]->(:TaxonomyConcept {pref_label: $pref, alt_labels: $alts})",
                tid=taxonomy_id, pref=cluster["pref_label"], alts=cluster["alt_labels"],
            )

    return taxonomy_id
