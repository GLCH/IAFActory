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
l'ANALYSE (liste + suggestions de regroupement par similarite d'embedding) -
PAS encore l'application automatique, volontairement laissee en discussion
avec l'utilisateur avant d'ecrire une action irreversible sur potentiellement
des milliers de noeuds Entity (voir docs/epics/EPIC-IAF-E7-classification-documents.md
US7.11)."""
from __future__ import annotations

from .graph import cosine_similarity, embed

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
