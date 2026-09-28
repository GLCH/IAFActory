"""Modele de donnees minimal (US5.2, US3.1). Le reste (projets, agents,
exclusions, connecteurs...) suit epic par epic, pas invente ici a l'avance."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Role(str, enum.Enum):
    viewer = "viewer"
    creator = "creator"
    admin = "admin"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role, name="role"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class DocumentStatus(str, enum.Enum):
    """US3.1/US7.4/US7.5. `recognized` = rattache a une classe existante deja
    peuplee par d'autres documents ; `provisional` = classe nouvellement creee
    a partir de ce document (non reconnu). Neo4j reste la source de verite du
    graphe (chunks, entites, classe) ; cette table ne fait que suivre le statut
    et la propriete cote application (ADR 0001)."""

    received = "received"
    ingested = "ingested"
    recognized = "recognized"
    provisional = "provisional"
    error = "error"


class Document(Base):
    """US3.1. `neo4j_class_id` et `sha256` relient cette ligne au graphe
    (noeuds Document et DocumentClass de Neo4j) ; aucun contenu de document
    n'est duplique en base relationnelle."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"), default=DocumentStatus.received, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    neo4j_class_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    class_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    entity_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PipelineRunStatus(str, enum.Enum):
    """IAF-E13 US13.1, version simplifiee : une ligne par execution du
    pipeline COMPLET (I-R-C-S), pas encore le detail par etape (`stage_runs`
    de l'ADR 0006) - a affiner si le besoin de reprise fine se confirme."""

    queued = "queued"
    running = "running"
    done = "done"
    error = "error"


class PipelineRun(Base):
    """IAF-E13 US13.1/US13.7. Execute par un ThreadPoolExecutor DANS le
    process uvicorn (worker.py) - pas un service separe (option B/C de
    l'ADR 0006 non retenues). Limite assumee : un run `running` au moment
    d'un redemarrage du serveur reste bloque dans cet etat (pas de reprise
    automatique, US13.6 reste a faire)."""

    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    status: Mapped[PipelineRunStatus] = mapped_column(
        Enum(PipelineRunStatus, name="pipeline_run_status"), default=PipelineRunStatus.queued, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PipelineStep(Base):
    """Ajoutee le 2026-09-28 (suite a la demande de detail par etape) : trace
    UNE etape reelle du pipeline (Ingestion/Structuration - voir pipeline.py,
    `ingest_document(..., on_step=...)`). Complement de `PipelineRun` (statut
    global uniquement) - PAS un remplacement de l'ADR 0006 `stage_runs`
    (reprise fine par etape reste hors scope, seulement de la visibilite).
    Volontairement pas d'etape "Exposition" ici : cette phase a lieu au
    moment d'une question du viewer (routers/ask.py), pas au depot d'un
    document - inventer une etape ici serait mentir sur ce que fait le
    systeme (regle du projet : pas de resultat invente)."""

    __tablename__ = "pipeline_steps"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)  # "Ingestion" ou "Structuration" (vocabulaire du doc d'architecture)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class PlatformSettings(Base):
    """IAF-E7 US7.6 (etendue le 2026-09-28) : "dans les parametres on peut
    autoriser le merge automatique et definir 2 seuils" - ligne UNIQUE
    (id=1 impose), reglable par le creator via /creator/settings. Pas de
    notion multi-tenant ici (coherent avec l'absence de "projet", US3.9)."""

    __tablename__ = "platform_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    auto_merge_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Seuil "sans doute" : fusion automatique si auto_merge_enabled.
    auto_merge_threshold: Mapped[float] = mapped_column(Float, default=0.97, nullable=False)
    # Seuil de suggestion (entre les deux : proposition, jamais automatique).
    suggest_merge_threshold: Mapped[float] = mapped_column(Float, default=0.90, nullable=False)
