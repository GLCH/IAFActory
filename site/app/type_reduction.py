"""Reduction du vocabulaire des TYPES D'ENTITES (Entity.type) - ajoute le
2026-09-30, demande explicite : "ajouter une fonctionnalite de normalisation
lexicale pour reduire le nombre d'entity types [...] on doit bien distinguer
les entites possibles des entites types".

Trois vocabulaires DISTINCTS coexistent dans ce systeme, a ne pas confondre :
1. Les ENTITES elles-memes (instances nommees, ex. "Sensorimotor Contingency
   Theory", "counting") - Entity.name, jamais reduites ici, ce sont les
   donnees.
2. Les CONCEPTS du vocabulaire (US7.1) - deja des owl:Class par classe
   (US7.5), deja normalises depuis le 2026-09-29 (pipeline._TypeNormalizer +
   reutilisation des concepts deja connus dans le prompt).
3. Les TYPES D'ENTITES (Entity.type, ex. "Theory", "Concept", "Process") -
   un vocabulaire libre invente par le LLM a CHAQUE extraction par chunk
   (US3.4), jamais encore ecrit comme owl:Class, souvent tres fragmente
   (constate reellement sur une seule classe : "Concept" (4), "Variable" (4),
   "Terme" (2), "Norme au carre" (1), "Coefficient" (1)...). C'EST le
   vocabulaire que ce module aide a reduire.

Le normaliseur temps reel (pipeline._TypeNormalizer, ajoute le 2026-09-29)
empeche seulement la CROISSANCE future du vocabulaire pendant l'ingestion -
il ne reduit pas retroactivement ce qui existe deja. Ce module fournit
l'ANALYSE (liste + suggestions de regroupement par similarite d'embedding)
ET, decide avec l'utilisateur le 2026-09-30, l'APPLICATION : classification
ascendante hierarchique en UN lot (meme methode que US3.19 pour la
taxonomie, scipy) avec apercu complet avant ecriture, PUIS un bouton unique
qui applique tout le lot. Les types canoniques resultants deviennent de
vraies owl:Class (unifie enfin ce vocabulaire avec celui des concepts,
US7.1 - decide avec l'utilisateur le meme jour)."""
from __future__ import annotations

from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist

from . import ontology
from .graph import cosine_similarity, embed

# Seuil de FUSION reelle (distance cosinus = 1 - similarite).
# Bug reel trouve en testant (2026-09-30) : fixe d'abord a 0.15 (0.85 de
# similarite, coherent avec pipeline.NORMALIZE_MATCH_THRESHOLD, le
# normaliseur temps reel) - mais AUCUNE des paires deja montrees comme
# suggestions pertinentes (75-84% de similarite, ex. "concept"/"Mathematical
# Concept" 82%) n'atteignait ce seuil : l'apercu produisait "0 groupe"
# malgre des doublons evidents. Le seuil temps reel (US-a-la-volee, un seul
# candidat compare a la fois) et le seuil du LOT AVEC APERCU (le creator
# voit tout et peut refuser en bloc) n'ont pas besoin d'etre aussi stricts -
# aligne desormais sur SUGGEST_DISPLAY_THRESHOLD ci-dessous, deja valide
# comme produisant des suggestions sensees. NON calibre, comme le reste
# (US7.7).
MERGE_DISTANCE = 0.25

# Seuil d'AFFICHAGE des suggestions - plus permissif que
# pipeline.NORMALIZE_MATCH_THRESHOLD (0.85, qui sert a la fusion AUTOMATIQUE
# et silencieuse en cours d'ingestion) : ici, une paire est montree pour
# DISCUSSION humaine, pas appliquee, un filet plus large est defendable.
# NON calibre, comme le reste du projet (US7.7).
SUGGEST_DISPLAY_THRESHOLD = 0.75


def list_entity_types(session, class_id: str) -> list[dict]:
    """Types d'entites DISTINCTS de cette classe, avec leur nombre d'usages -
    jamais les entites (instances) elles-memes. Trie par frequence
    decroissante : les types les plus rares (la longue traine) sont ceux les
    plus susceptibles d'etre des quasi-doublons accidentels d'un type deja
    etabli."""
    rows = session.run(
        "MATCH (e:Entity {class_id: $cid}) WHERE e.type IS NOT NULL "
        "RETURN e.type AS type, count(e) AS n ORDER BY n DESC",
        cid=class_id,
    )
    return [{"type": r["type"], "count": r["n"]} for r in rows]


def suggest_type_merges(entity_types: list[dict]) -> list[dict]:
    """Paires de types assez proches par embedding pour etre discutees.
    AUCUNE fusion appliquee ici - reutilise la meme mesure de similarite que
    pipeline._TypeNormalizer (coherence de methode dans tout le projet)."""
    labels = [t["type"] for t in entity_types]
    if len(labels) < 2:
        return []
    vectors = {label: embed(label) for label in labels}
    suggestions = []
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            score = cosine_similarity(vectors[a], vectors[b])
            if score >= SUGGEST_DISPLAY_THRESHOLD:
                suggestions.append({"a": a, "b": b, "score": score})
    suggestions.sort(key=lambda s: s["score"], reverse=True)
    return suggestions


def build_reduction_plan(entity_types: list[dict]) -> list[dict]:
    """Classification ascendante hierarchique (memes outils que
    taxonomy_builder.py, US3.19) en UN SEUL niveau (pas de hierarchie
    broader/narrower ici - juste des groupes de synonymes a fusionner).
    Renvoie une liste de groupes {"canonical": str, "members": [...],
    "total_count": int} - un groupe d'un seul membre = deja isole, aucun
    changement pour lui. Le libelle canonique = le membre le PLUS FREQUENT du
    groupe (signal d'usage reel deja disponible, contrairement aux concepts
    de la taxonomie ou "le plus court" etait la seule heuristique possible)."""
    if len(entity_types) < 2:
        return [{"canonical": t["type"], "members": [t], "total_count": t["count"]} for t in entity_types]

    vectors = [embed(t["type"]) for t in entity_types]
    distance = pdist(vectors, metric="cosine")
    z = linkage(distance, method="average")
    cluster_ids = fcluster(z, t=MERGE_DISTANCE, criterion="distance")

    groups: dict[int, list[dict]] = {}
    for entity_type, cid in zip(entity_types, cluster_ids):
        groups.setdefault(int(cid), []).append(entity_type)

    plan = []
    for members in groups.values():
        canonical = max(members, key=lambda t: t["count"])["type"]
        plan.append({
            "canonical": canonical,
            "members": sorted(members, key=lambda t: -t["count"]),
            "total_count": sum(t["count"] for t in members),
        })
    plan.sort(key=lambda g: -g["total_count"])
    return plan


def apply_reduction_plan(session, class_id: str, plan: list[dict], language: str = "fr") -> tuple[int, int]:
    """Applique le plan : renomme Entity.type pour chaque membre non-canonique
    d'un groupe fusionne, et ecrit CHAQUE type canonique (fusionne ou deja
    isole) comme un vrai concept - owl:Class dans Fuseki + noeud Concept
    Neo4j fusionne (MERGE) avec le vocabulaire existant (US7.1) - decide avec
    l'utilisateur le 2026-09-30. Renvoie (nombre de types renommes, nombre
    d'entites affectees)."""
    n_types_renamed = 0
    n_entities_renamed = 0
    for group in plan:
        canonical = group["canonical"]
        vector = embed(canonical)
        try:
            uri = ontology.ensure_concept(class_id, canonical, language)
        except Exception:
            uri = None  # Fuseki indisponible : le concept Neo4j est ecrit quand meme (US3.4, meme principe)
        session.run(
            "MERGE (c:DocumentClass {id: $cid}) "
            "MERGE (concept:Concept {label: $label, class_id: $cid}) "
            "SET concept.embedding = $embedding, concept.uri = coalesce($uri, concept.uri) "
            "MERGE (c)-[:HAS_CONCEPT]->(concept)",
            cid=class_id, label=canonical, uri=uri, embedding=vector,
        )
        if len(group["members"]) <= 1:
            continue
        for member in group["members"]:
            if member["type"] == canonical:
                continue
            result = session.run(
                "MATCH (e:Entity {class_id: $cid, type: $old}) SET e.type = $canonical RETURN count(e) AS n",
                cid=class_id, old=member["type"], canonical=canonical,
            ).single()
            n_entities_renamed += result["n"]
            n_types_renamed += 1
    return n_types_renamed, n_entities_renamed
