"""Ecriture des concepts semantiques induits comme de vraies classes OWL dans
Fuseki (ADR 0001 : Fuseki = source de verite des ontologies RDF/OWL/SPARQL).
IAF-E7 US7.5 : "schema normalise publie comme ontologie semantique induite
dans un graphe nomme Fuseki" - deja specifie depuis le 2026-09-26, jamais
implemente avant ce tour (le pipeline ne touchait que Neo4j).

Un graphe nomme par classe documentaire : http://iafactory.local/classes/{id}.
Neo4j reste la lecture rapide pour les ecrans du site (pipeline.py,
routers/documents.py) ; Fuseki est la source de verite RDF, ecrite en
parallele, pas encore relue par le site (US3.14 : a faire si le besoin de
lire directement le SPARQL se confirme)."""
from __future__ import annotations

import re
import unicodedata
import uuid

import requests

from .config import settings

_PREFIXES = (
    "PREFIX owl: <http://www.w3.org/2002/07/owl#>\n"
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
    "PREFIX skos: <http://www.w3.org/2004/02/skos/core#>\n"
    "PREFIX iafs: <urn:iaf:ns:structure#>\n"
)

# US3.11 (IAF-92) : vocabulaire structurel COMMUN (ontologies/structure/
# iaf-structure-base.ttl et ses specialisations), un seul exemplaire
# partage par toutes les classes - jamais duplique par classe (voir
# load_turtle_file/ensure_class_accepts_structure ci-dessous). livre/word/pdf/
# latex (2026-10-01) portent aussi les axiomes de cardinalite utilises par
# app/structure_matcher.py (US7.2-structurel).
STRUCTURE_BASE_GRAPH = "http://iafactory.local/ontology/structure/base"
STRUCTURE_LIVRE_GRAPH = "http://iafactory.local/ontology/structure/livre"
STRUCTURE_WORD_GRAPH = "http://iafactory.local/ontology/structure/word"
STRUCTURE_PDF_GRAPH = "http://iafactory.local/ontology/structure/pdf"
STRUCTURE_LATEX_GRAPH = "http://iafactory.local/ontology/structure/latex"

# IAF-E7 US7.9 : relations et attributs extraits (US13.4) comme de vraies
# proprietes OWL, a PORTEE GLOBALE (une seule definition reutilisee par
# toutes les classes qui l'emploient) - decide le 2026-09-28, par opposition
# aux concepts qui restent par classe (US7.5).
PROPERTIES_GRAPH = "http://iafactory.local/ontology/properties"


def _run_update(update: str) -> None:
    r = requests.post(
        f"{settings.fuseki_url}/{settings.fuseki_dataset}/update",
        data=update.encode("utf-8"),
        headers={"Content-Type": "application/sparql-update; charset=utf-8"},
        timeout=15,
    )
    r.raise_for_status()


def _run_query(query: str) -> list[dict]:
    r = requests.get(
        f"{settings.fuseki_url}/{settings.fuseki_dataset}/sparql",
        params={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get("results", {}).get("bindings", [])


def _slug(label: str) -> str:
    # Accents retires avant le filtre alphanumerique (sinon "e" accentue
    # produit un "-" disgracieux dans l'URI, ex. "syst-me" au lieu de
    # "systeme" - constate reellement au premier test, corrige aussitot).
    decomposed = unicodedata.normalize("NFKD", label.strip().lower())
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-")
    return slug or uuid.uuid4().hex[:8]


def class_graph_uri(class_id: str) -> str:
    return f"http://iafactory.local/classes/{class_id}"


def concept_uri(class_id: str, label: str) -> str:
    return f"{class_graph_uri(class_id)}#{_slug(label)}"


def ensure_concept(class_id: str, label: str, language: str = "fr") -> str:
    """Ecrit (idempotent : INSERT DATA d'un triple deja present est un
    no-op) le concept comme une classe OWL dans le graphe nomme de la classe
    documentaire. Renvoie l'URI du concept.

    `language` : bug reel corrige le 2026-09-29 - le tag RDF etait TOUJOURS
    `@fr`, meme pour un concept induit sur un document en anglais (etiquette
    RDF factuellement fausse). Code ISO 639-1 detecte par le LLM lors de
    l'extraction du vocabulaire (US7.1) ; repli sur "fr" si absent/inconnu."""
    uri = concept_uri(class_id, label)
    graph = class_graph_uri(class_id)
    label_escaped = label.replace('"', '\\"')
    lang_tag = (language or "fr").strip().lower()[:2] or "fr"
    update = (
        f"{_PREFIXES}"
        f"INSERT DATA {{ GRAPH <{graph}> {{ <{uri}> a owl:Class ; rdfs:label \"{label_escaped}\"@{lang_tag} . }} }}"
    )
    r = requests.post(
        f"{settings.fuseki_url}/{settings.fuseki_dataset}/update",
        data=update.encode("utf-8"),
        headers={"Content-Type": "application/sparql-update; charset=utf-8"},
        timeout=15,
    )
    r.raise_for_status()
    return uri


def ensure_subclass(class_id: str, child_label: str, parent_label: str) -> None:
    """2026-10-02 : `rdfs:subClassOf` entre deux concepts (owl:Class) du
    graphe de la classe - la hierarchie REELLE d'une ontologie importee,
    jusqu'ici perdue a l'import. INSERT DATA idempotent."""
    graph = class_graph_uri(class_id)
    update = (
        f"{_PREFIXES}INSERT DATA {{ GRAPH <{graph}> {{ "
        f"<{concept_uri(class_id, child_label)}> rdfs:subClassOf <{concept_uri(class_id, parent_label)}> . }} }}"
    )
    _run_update(update)


def set_concept_definition(
    class_id: str, label: str, definition: str, language: str = "en", source: str | None = None,
) -> None:
    """2026-10-02 : ecrit UNIQUEMENT la definition (`rdfs:comment`) d'un
    concept, sans toucher a sa traduction (contrairement a
    set_concept_metadata) - pour les definitions importees d'un fichier ou
    d'un dictionnaire (`source` : mention conservee en `dcterms:source`, la
    licence de WordNet l'exige). Remplace la precedente (pas d'accumulation)."""
    uri = concept_uri(class_id, label)
    graph = class_graph_uri(class_id)
    lang = (language or "en").strip().lower()[:2] or "en"
    ops = [
        f"{_PREFIXES}DELETE WHERE {{ GRAPH <{graph}> {{ <{uri}> rdfs:comment ?c . }} }}",
        f"{_PREFIXES}DELETE WHERE {{ GRAPH <{graph}> {{ <{uri}> <http://purl.org/dc/terms/source> ?s . }} }}",
    ]
    if definition and source:
        ops.append(
            f"{_PREFIXES}INSERT DATA {{ GRAPH <{graph}> {{ <{uri}> <http://purl.org/dc/terms/source> "
            f"\"{_escape_literal(source)}\" . }} }}"
        )
    if definition:
        ops.append(
            f"{_PREFIXES}INSERT DATA {{ GRAPH <{graph}> {{ <{uri}> rdfs:comment "
            f"\"{_escape_literal(definition)}\"@{lang} . }} }}"
        )
    _run_update(" ; ".join(ops))


def set_concept_metadata(class_id: str, label: str, definition: str, translation_en: str) -> None:
    """US3.17 : definition (`rdfs:comment`) et traduction anglaise
    (`rdfs:label@en`) d'un concept, saisies par le creator - jamais induites
    par le LLM (US7.5 ne produit qu'un libelle francais). Remplace toute
    valeur precedente (DELETE puis INSERT, pas d'accumulation de doublons)."""
    uri = concept_uri(class_id, label)
    graph = class_graph_uri(class_id)
    # `DELETE WHERE` (forme raccourcie) ne supporte pas FILTER (erreur de
    # syntaxe SPARQL reelle rencontree ici) : forme complete DELETE/WHERE
    # pour la traduction, qui doit filtrer par langue.
    ops = [
        f"{_PREFIXES}DELETE WHERE {{ GRAPH <{graph}> {{ <{uri}> rdfs:comment ?c . }} }}",
        f"{_PREFIXES}DELETE {{ GRAPH <{graph}> {{ <{uri}> rdfs:label ?l . }} }} "
        f"WHERE {{ GRAPH <{graph}> {{ <{uri}> rdfs:label ?l . FILTER(lang(?l) = 'en') }} }}",
    ]
    inserts = []
    if definition:
        inserts.append(f'<{uri}> rdfs:comment "{definition.replace(chr(34), chr(92) + chr(34))}"@fr .')
    if translation_en:
        inserts.append(f'<{uri}> rdfs:label "{translation_en.replace(chr(34), chr(92) + chr(34))}"@en .')
    if inserts:
        ops.append(f"{_PREFIXES}INSERT DATA {{ GRAPH <{graph}> {{ {' '.join(inserts)} }} }}")

    r = requests.post(
        f"{settings.fuseki_url}/{settings.fuseki_dataset}/update",
        data=" ; ".join(ops).encode("utf-8"),
        headers={"Content-Type": "application/sparql-update; charset=utf-8"},
        timeout=15,
    )
    r.raise_for_status()


def list_concepts(class_id: str) -> list[dict]:
    """Concepts OWL deja ecrits pour cette classe (verification, pas encore
    utilise par les ecrans du site - voir US3.14)."""
    graph = class_graph_uri(class_id)
    query = (
        f"{_PREFIXES}"
        f"SELECT ?concept ?label WHERE {{ GRAPH <{graph}> {{ ?concept a owl:Class ; rdfs:label ?label . }} }}"
    )
    bindings = _run_query(query)
    return [{"uri": b["concept"]["value"], "label": b["label"]["value"]} for b in bindings]


def class_graph_has_content(class_id: str) -> bool:
    r = requests.get(
        f"{settings.fuseki_url}/{settings.fuseki_dataset}/sparql",
        params={"query": f"ASK {{ GRAPH <{class_graph_uri(class_id)}> {{ ?s ?p ?o }} }}"},
        headers={"Accept": "application/sparql-results+json"},
        timeout=15,
    )
    r.raise_for_status()
    return bool(r.json().get("boolean", False))


def load_turtle_file(graph_uri: str, turtle_content: bytes) -> None:
    """US3.11 (IAF-92) : charge un fichier .ttl entier dans un graphe nomme
    Fuseki via le Graph Store Protocol (POST .../data?graph=...) - AJOUTE au
    graphe (ne le remplace pas), idempotent pour un contenu inchange (un
    triple deja present est un no-op, RDF = un ensemble). Utilise pour
    charger le vocabulaire structurel commun (ontologies/structure/*.ttl),
    jamais implemente avant le 2026-09-30 (les fichiers restaient statiques
    sur disque, voir leur propre commentaire "non chargee dans Fuseki")."""
    r = requests.post(
        f"{settings.fuseki_url}/{settings.fuseki_dataset}/data",
        params={"graph": graph_uri},
        data=turtle_content,
        headers={"Content-Type": "text/turtle; charset=utf-8"},
        timeout=15,
    )
    r.raise_for_status()


def ensure_class_accepts_structure(class_id: str, structure_ontology_uri: str) -> None:
    """US3.11 (IAF-92) : relie une classe documentaire (son graphe nomme,
    utilise ici aussi comme IRI de ressource - meme convention que le reste
    du module) a une ontologie structurelle qu'elle accepte
    (`iafs:acceptsStructure`, repetable - une classe peut accepter plusieurs
    formes, ex. "flat" ET "livre"). INSERT DATA idempotent, comme
    ensure_concept."""
    graph = class_graph_uri(class_id)
    update = (
        f"{_PREFIXES}INSERT DATA {{ GRAPH <{graph}> "
        f"{{ <{graph}> iafs:acceptsStructure <{structure_ontology_uri}> . }} }}"
    )
    r = requests.post(
        f"{settings.fuseki_url}/{settings.fuseki_dataset}/update",
        data=update.encode("utf-8"),
        headers={"Content-Type": "application/sparql-update; charset=utf-8"},
        timeout=15,
    )
    r.raise_for_status()


def list_accepted_structures(class_id: str) -> list[str]:
    graph = class_graph_uri(class_id)
    query = f"{_PREFIXES}SELECT ?structure WHERE {{ GRAPH <{graph}> {{ <{graph}> iafs:acceptsStructure ?structure . }} }}"
    bindings = _run_query(query)
    return [b["structure"]["value"] for b in bindings]


def classes_accepting_structure(structure_uri: str) -> list[str]:
    """US7.2-structurel (2026-10-01, IAF-125) : l'inverse de
    list_accepted_structures - quelles classes acceptent CETTE ontologie
    structurelle, en UNE requete a travers tous les graphes nommes (pas une
    requete par classe). Le graphe d'une classe est litteralement son IRI
    (class_graph_uri), donc l'id de classe est recupere depuis le graphe
    trouve plutot que depuis un second aller-retour."""
    query = (
        f"{_PREFIXES}SELECT ?graph WHERE {{ GRAPH ?graph {{ "
        f"?graph iafs:acceptsStructure <{structure_uri}> . }} }}"
    )
    bindings = _run_query(query)
    class_ids = []
    for b in bindings:
        graph_uri = b["graph"]["value"]
        prefix = "http://iafactory.local/classes/"
        if graph_uri.startswith(prefix):
            class_ids.append(graph_uri[len(prefix):])
    return class_ids


# 2026-10-02 (demande explicite : "laisse l'utilisateur choisir l'export (ttl
# ou owl/xml)") : formats d'export proposes partout ou une ontologie ou une
# taxonomie se telecharge. Fuseki fait la serialisation (negociation de
# contenu), pas de conversion cote Python.
EXPORT_FORMATS = {
    "ttl": ("text/turtle", "ttl"),
    "owl": ("application/rdf+xml", "owl"),
}


def normalize_export_format(fmt: str | None) -> str:
    return fmt if fmt in EXPORT_FORMATS else "ttl"


def export_structural_ontology(structure_uri: str, fmt: str = "ttl") -> bytes | None:
    """2026-10-02 : telechargement d'une ontologie structurelle (Turtle ou
    RDF/XML au choix) depuis la page detail d'une classe. Retrouve le graphe
    nomme Fuseki qui declare cette `owl:Ontology` (ces ontologies sont
    COMMUNES, un graphe par ontologie - voir load_turtle_file) puis en exporte
    tous les triples (CONSTRUCT). Renvoie None si aucun graphe ne la declare."""
    lookup = f"{_PREFIXES}SELECT ?g WHERE {{ GRAPH ?g {{ <{structure_uri}> a owl:Ontology }} }} LIMIT 1"
    bindings = _run_query(lookup)
    if not bindings:
        return None
    graph = bindings[0]["g"]["value"]
    r = requests.get(
        f"{settings.fuseki_url}/{settings.fuseki_dataset}/sparql",
        params={"query": f"CONSTRUCT {{ ?s ?p ?o }} WHERE {{ GRAPH <{graph}> {{ ?s ?p ?o }} }}"},
        headers={"Accept": EXPORT_FORMATS[normalize_export_format(fmt)][0]},
        timeout=15,
    )
    r.raise_for_status()
    return r.content


def describe_structural_ontologies(class_id: str) -> list[dict]:
    """US3.14/IAF-92 (2026-10-01) : distinction explicite ontologie(s)
    STRUCTURELLE(s) (forme) vs ontologie SEMANTIQUE (contenu, list_concepts)
    - demande explicite "Ajoute cette distinction dans le systeme". Pour
    chaque ontologie structurelle acceptee par la classe, lit son libelle et
    les formats de fichier auxquels elle s'applique (`iafs:appliesToFormat`,
    recherche sur TOUS les graphes nommes, pas seulement celui de la classe -
    ces ontologies sont COMMUNES, voir load_turtle_file)."""
    results = []
    for structure_uri in list_accepted_structures(class_id):
        query = (
            f"{_PREFIXES}SELECT ?label ?format WHERE {{ GRAPH ?g {{ "
            f"<{structure_uri}> rdfs:label ?label . "
            f"OPTIONAL {{ <{structure_uri}> iafs:appliesToFormat ?format }} }} }}"
        )
        bindings = _run_query(query)
        if not bindings:
            continue
        label = bindings[0]["label"]["value"]
        formats = sorted({b["format"]["value"] for b in bindings if "format" in b})
        results.append({"uri": structure_uri, "label": label, "formats": formats})
    return results


def export_class_owl(class_id: str, fmt: str = "owl") -> bytes:
    """US3.16, bug reel corrige le 2026-09-29 (signale par l'utilisateur :
    "dans l'ontologie, on n'a toujours pas d'object properties alors qu'il y
    a des relations associees"). Les concepts (owl:Class, US7.5) et les
    proprietes (owl:ObjectProperty/DatatypeProperty, US7.9) vivent dans deux
    graphes nommes SEPARES depuis leur conception meme - decide le
    2026-09-28, "proprietes partagees globalement, pas par classe". Mais
    l'export ne recuperait QUE le graphe de la classe : les proprietes
    existaient bien dans Fuseki (verifie directement le 2026-09-29 - 836
    owl:ObjectProperty et 352 owl:DatatypeProperty presents) mais
    n'apparaissaient jamais dans un export telecharge, malgre les relations
    associees visibles dans le graphe de connaissance (Neo4j). Corrige par
    une requete SPARQL CONSTRUCT fusionnant les deux graphes nommes - Fuseki
    fait la fusion et la serialisation RDF/XML, pas de fusion manuelle cote
    Python. Chaque classe voit donc le meme vocabulaire de proprietes
    partage (coherent avec la decision "global", pas de domain/range par
    classe dans cette version - question deja ouverte dans US7.9)."""
    query = (
        "CONSTRUCT { ?s ?p ?o } WHERE { "
        f"{{ GRAPH <{class_graph_uri(class_id)}> {{ ?s ?p ?o }} }} UNION "
        f"{{ GRAPH <{PROPERTIES_GRAPH}> {{ ?s ?p ?o }} }} }}"
    )
    r = requests.get(
        f"{settings.fuseki_url}/{settings.fuseki_dataset}/sparql",
        params={"query": query},
        headers={"Accept": EXPORT_FORMATS[normalize_export_format(fmt) if fmt else "owl"][0]},
        timeout=30,
    )
    r.raise_for_status()
    return r.content


def property_uri(label: str) -> str:
    return f"{PROPERTIES_GRAPH}#{_slug(label)}"


def _ensure_global_property(label: str, owl_type: str) -> str:
    """IAF-E7 US7.9. `owl_type` = "owl:ObjectProperty" ou "owl:DatatypeProperty".
    Portee globale (PROPERTIES_GRAPH), idempotent comme ensure_concept."""
    uri = property_uri(label)
    label_escaped = label.replace('"', '\\"')
    update = (
        f"{_PREFIXES}"
        f"INSERT DATA {{ GRAPH <{PROPERTIES_GRAPH}> {{ <{uri}> a {owl_type} ; rdfs:label \"{label_escaped}\"@fr . }} }}"
    )
    _run_update(update)
    return uri


def ensure_relation_property(label: str) -> str:
    return _ensure_global_property(label, "owl:ObjectProperty")


def ensure_attribute_property(label: str) -> str:
    return _ensure_global_property(label, "owl:DatatypeProperty")


def list_properties(owl_type: str) -> list[str]:
    """Ajoute le 2026-09-29 : libelles des proprietes globales deja connues
    (owl_type = "owl:ObjectProperty" ou "owl:DatatypeProperty"), pour la
    normalisation par similarite d'embedding des types de relations/attributs
    extraits (demande explicite : reduire le nombre d'ontologies quasi-
    doublons en comparant aux termes deja connus)."""
    query = (
        f"{_PREFIXES}"
        f"SELECT ?label WHERE {{ GRAPH <{PROPERTIES_GRAPH}> {{ ?p a {owl_type} ; rdfs:label ?label . }} }}"
    )
    bindings = _run_query(query)
    return [b["label"]["value"] for b in bindings]


def _escape_literal(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def taxonomy_graph_uri(taxonomy_id: str) -> str:
    return f"http://iafactory.local/taxonomies/{taxonomy_id}"


def write_taxonomy(taxonomy_id: str, name: str, tree: list[dict]) -> None:
    """US3.18, hierarchie ajoutee US3.19 (2026-09-28, revient sur la decision
    "liste plate" prise le meme jour dans US3.18 - l'utilisateur a redemande
    une vraie classification hierarchique). `tree` : liste de concepts de TETE,
    chacun {"pref_label": str, "alt_labels": [str,...], "children": [meme
    forme, "children" vide - 2 niveaux dans cette version]}. Un concept de
    tete SANS enfant est un concept isole (aucun regroupement plus general
    trouve pour lui) - meme resultat visuel qu'une liste plate (US3.18), pas
    de niveau fictif invente pour un concept qui n'en a pas besoin. Le graphe
    nomme EST le `skos:ConceptScheme` (URI coherente et stable pour le
    telechargement, US3.16/US3.18)."""
    graph = taxonomy_graph_uri(taxonomy_id)
    triples = [f'<{graph}> a skos:ConceptScheme ; rdfs:label "{_escape_literal(name)}"@fr .']
    used_slugs: set[str] = set()

    def _unique_uri(label: str) -> str:
        # Bug reel constate le 2026-09-28 : le LLM charge de nommer un groupe
        # general (US3.19) peut choisir un libelle identique a celui d'un de
        # ses membres (ex. groupe {"Fonction", "Fonction de perte"} nomme
        # "Fonction") - sans ce garde-fou, le concept general et le concept
        # fin partagent la MEME URI (meme slug), et se retrouvent chacun
        # skos:broader/skos:narrower d'EUX-MEMES dans l'export SKOS.
        slug = _slug(label)
        candidate, n = slug, 2
        while candidate in used_slugs:
            candidate = f"{slug}-{n}"
            n += 1
        used_slugs.add(candidate)
        return f"{graph}#{candidate}"

    def _concept_triples(node: dict, parent_uri: str | None) -> None:
        uri = _unique_uri(node["pref_label"])
        triples.append(
            f'<{uri}> a skos:Concept ; skos:inScheme <{graph}> ; '
            f'skos:prefLabel "{_escape_literal(node["pref_label"])}"@fr .'
        )
        for alt in node["alt_labels"]:
            if alt != node["pref_label"]:
                triples.append(f'<{uri}> skos:altLabel "{_escape_literal(alt)}"@fr .')
        if node.get("definition"):
            triples.append(f'<{uri}> skos:definition "{_escape_literal(node["definition"])}" .')
        if parent_uri is None:
            triples.append(f'<{graph}> skos:hasTopConcept <{uri}> .')
            triples.append(f'<{uri}> skos:topConceptOf <{graph}> .')
        else:
            triples.append(f'<{uri}> skos:broader <{parent_uri}> .')
            triples.append(f'<{parent_uri}> skos:narrower <{uri}> .')
        for child in node.get("children", []):
            _concept_triples(child, uri)

    for top in tree:
        _concept_triples(top, None)

    update = f"{_PREFIXES}INSERT DATA {{ GRAPH <{graph}> {{ {' '.join(triples)} }} }}"
    _run_update(update)


def export_taxonomy_turtle(taxonomy_id: str, fmt: str = "ttl") -> bytes:
    """Export SKOS (Turtle par defaut, RDF/XML au choix) du graphe nomme de la taxonomie, depuis Fuseki
    (source de verite) - pas regenere depuis Neo4j (meme principe que
    class_ontology_owl, US3.16)."""
    graph = taxonomy_graph_uri(taxonomy_id)
    r = requests.get(
        f"{settings.fuseki_url}/{settings.fuseki_dataset}/data",
        params={"graph": graph},
        headers={"Accept": EXPORT_FORMATS[normalize_export_format(fmt)][0]},
        timeout=15,
    )
    r.raise_for_status()
    return r.content


def merge_class_ontology(source_class_id: str, target_class_id: str) -> None:
    """IAF-E7 US7.6 (etendue) : fusion de deux classes - transfere le contenu
    RDF du graphe source vers le graphe cible (ADD, sans ecraser ce qui existe
    deja) puis vide le graphe source. L'URI du graphe source reste valide
    (coherence des URI demandee explicitement) mais devient vide - un
    telechargement ulterieur renvoie une ontologie vide, pas une erreur.

    EPIC-IAF-E17 US17.3 (2026-10-02) : les ontologies structurelles acceptees
    (`<graphe> iafs:acceptsStructure <ontologie>`) ont pour SUJET le graphe de
    la classe lui-meme ; le ADD copiait donc `<graphe SOURCE> acceptsStructure
    X` dans le graphe cible (sujet faux, jamais lu). Elles sont reecrites avec
    pour sujet le graphe CIBLE (union : deux ontologies identiques ne forment
    qu'une entree, des ontologies differentes sont toutes conservees) avant de
    vider la source."""
    source = class_graph_uri(source_class_id)
    target = class_graph_uri(target_class_id)
    _run_update(
        f"{_PREFIXES}ADD <{source}> TO <{target}> ; "
        f"INSERT {{ GRAPH <{target}> {{ <{target}> iafs:acceptsStructure ?x }} }} "
        f"WHERE {{ GRAPH <{target}> {{ <{source}> iafs:acceptsStructure ?x }} }} ; "
        f"DELETE WHERE {{ GRAPH <{target}> {{ <{source}> iafs:acceptsStructure ?x }} }} ; "
        f"CLEAR GRAPH <{source}>"
    )


def delete_concept(class_id: str, label: str, uri: str | None = None) -> None:
    """Ajoute le 2026-09-28 (gouvernance : purger un concept mal induit sans
    supprimer toute la classe). `DELETE WHERE` (forme raccourcie) est valide
    ICI - le bug reel trouve le 2026-09-28 (US3.17) concernait uniquement son
    usage avec FILTER, pas ce cas simple (un seul triple pattern).

    `uri` (2026-10-02, bug reel trouve en appliquant la reduction d'ontologie
    sur une classe fusionnee : Neo4j perdait 32 concepts mais Fuseki gardait
    les 43 owl:Class) : apres une FUSION de classes, un concept garde dans le
    graphe Fuseki fusionne l'URI de sa classe d'ORIGINE (`.../classes/{source}#slug`,
    coherence des URI voulue, voir merge_class_ontology) - recalculer l'URI avec
    l'id de la classe CIBLE ne retrouvait rien. L'URI reelle est stockee sur le
    noeud `Concept.uri` : l'appelant la fournit ; a defaut, repli sur l'URI
    calculee (classes jamais fusionnees)."""
    uri = uri or concept_uri(class_id, label)
    graph = class_graph_uri(class_id)
    # Plusieurs URI peuvent porter le MEME libelle dans un graphe issu de
    # fusions (une par classe d'origine, alors que Neo4j n'a qu'un noeud
    # Concept par libelle) : on retire donc aussi toute owl:Class de ce libelle.
    escaped = _escape_literal(label)
    _run_update(
        f"{_PREFIXES}DELETE WHERE {{ GRAPH <{graph}> {{ <{uri}> ?p ?o . }} }} ; "
        f"DELETE {{ GRAPH <{graph}> {{ ?c ?p ?o }} }} WHERE {{ GRAPH <{graph}> {{ "
        f"?c a owl:Class ; rdfs:label ?l . FILTER(STR(?l) = \"{escaped}\") ?c ?p ?o }} }}"
    )


def delete_class_ontology(class_id: str) -> None:
    """Vide (et laisse vide, pas supprime - meme principe que
    merge_class_ontology) le graphe nomme d'une classe supprimee cote Neo4j.
    SILENT : une classe dont l'ecriture Fuseki avait deja echoue a l'ingestion
    (Fuseki indisponible, cf. pipeline.py) n'a pas de graphe a vider - ne doit
    pas faire echouer la suppression pour autant."""
    _run_update(f"CLEAR SILENT GRAPH <{class_graph_uri(class_id)}>")


def delete_taxonomy(taxonomy_id: str) -> None:
    _run_update(f"CLEAR SILENT GRAPH <{taxonomy_graph_uri(taxonomy_id)}>")


def list_graphs() -> list[str]:
    """Ajoute le 2026-09-29 (archivage admin) : tous les graphes nommes du
    dataset (classes, proprietes globales, taxonomies)."""
    bindings = _run_query("SELECT DISTINCT ?g WHERE { GRAPH ?g { ?s ?p ?o } }")
    return [b["g"]["value"] for b in bindings]


def export_graph_turtle(graph_uri: str) -> bytes:
    """Export Turtle d'UN graphe nomme quelconque (archivage admin - plus
    generique que export_taxonomy_turtle/class_ontology_owl, qui restent pour
    l'usage creator normal)."""
    r = requests.get(
        f"{settings.fuseki_url}/{settings.fuseki_dataset}/data",
        params={"graph": graph_uri},
        headers={"Accept": "text/turtle"},
        timeout=30,
    )
    r.raise_for_status()
    return r.content


def wipe_all() -> None:
    """Admin uniquement (routers/admin.py, demande explicite "vider
    completement le service"). Supprime TOUS les graphes nommes du dataset
    (classes, proprietes globales, taxonomies) - irreversible ; l'appelant
    est responsable d'archiver avant si besoin (voir archive_all, admin.py)."""
    _run_update("DROP SILENT ALL")
