"""Contrat de sortie du service d'exposition (typé, sérialisable : c'est le schéma des outils MCP)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Source(BaseModel):
    class_id: str
    class_name: str
    document: str | None = None
    section: str | None = None


class Evidence(BaseModel):
    """Une donnee enregistree utilisee pour repondre. `route` : « graphe » (concept, definition, relation,
    attribut, hierarchie : donnees semantiques) ou « rag » (passage de document retrouve)."""

    n: int
    kind: Literal["definition", "hierarchie", "attribut", "relation", "passage"]
    route: Literal["graphe", "rag"]
    text: str
    source: Source | None = None


class Match(BaseModel):
    kind: Literal["concept", "entite", "document"]
    label: str
    detail: str | None = None
    class_id: str
    class_name: str


Status = Literal["repondu", "donnees_insuffisantes", "terme_sans_detail", "inconnu", "requete_trop_courte"]


class Answer(BaseModel):
    """Resultat d'une recherche. Tout `answer` / `explanation` vient UNIQUEMENT des `evidence` (donnees
    enregistrees). Hors `repondu`, `ignorance` explique ce que le systeme ne sait pas, sans appel au modele."""

    query: str
    status: Status
    answer: str | None = None
    explanation: str | None = None
    limits: str | None = None
    ignorance: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    matches: list[Match] = Field(default_factory=list)
    routes: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)
    enrichment: Literal["llm", "deterministe", "non_applicable"] = "non_applicable"
    notes: list[str] = Field(default_factory=list)


class ClassSummary(BaseModel):
    id: str
    name: str
    status: str
    documents: int
    concepts: int
    entities: int


class ConceptRow(BaseModel):
    label: str
    definition: str | None = None
    support: int = 0


class EntityTypeRow(BaseModel):
    type: str
    count: int
    examples: list[str] = Field(default_factory=list)


class DocumentRow(BaseModel):
    filename: str | None = None
    title: str | None = None
    language: str | None = None
    ingested_at: str | None = None


class ClassDetail(BaseModel):
    id: str
    name: str
    status: str
    concepts: list[ConceptRow] = Field(default_factory=list)
    entity_types: list[EntityTypeRow] = Field(default_factory=list)
    documents: list[DocumentRow] = Field(default_factory=list)
