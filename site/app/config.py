"""Configuration du site. Lit le `.env` a la racine du depot (memes secrets
que le reste de l'infra) plus un `site/.env` optionnel propre au site. Quand
ces fichiers n'existent pas (dans l'image Docker), les valeurs viennent des
variables d'environnement passees par `compose.yaml`. Aucun secret n'a de
valeur par defaut en dur.

POSTGRES_HOST/POSTGRES_PORT valent par defaut 127.0.0.1:5432 (usage local,
hors Docker, ou POSTGRES_PORT vient du `.env` racine et peut avoir ete
decale pour eviter un conflit de port sur la machine). Dans `compose.yaml`,
le service `site` les surcharge en `postgres`:5432 (reseau interne Docker,
jamais le port hote remappe)."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SITE_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", SITE_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_host: str = "127.0.0.1"
    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_port: int = 5432

    session_secret: str
    session_ttl_hours: int = 12

    # Neo4j (graph RAG, IAF-E3) : memes conventions que Postgres ci-dessus -
    # 127.0.0.1 par defaut pour un usage local hors Docker (port NEO4J_BOLT_PORT
    # du .env racine, deja decale sur cette machine) ; `neo4j_host` surcharge en
    # `neo4j`:7687 (port interne fixe) dans compose.yaml.
    neo4j_host: str = "127.0.0.1"
    neo4j_port: int = 7687
    neo4j_password: str

    # Passerelle LLM (ADR 0004). Alias existants de infra/llm-gateway/litellm.yaml,
    # jamais un nom de modele en dur. Defaut = "gemini-vertex-flash" (Gemini
    # via Vertex AI, compte de service fourni le 2026-09-28, teste reellement
    # via /v1/chat/completions - reponse "OK" recue) : decide par l'utilisateur
    # le 2026-09-28 a la place d'"ollama-local" (local, gratuit, mais lent -
    # plusieurs minutes par document, timeouts frequents constates cette
    # session) pour la vitesse et la fiabilite. Cout reel mais faible avec
    # Flash ; aucun budget par agent (US11.3) n'est encore applique par ce
    # site - a garder en tete si le volume de documents augmente.
    llm_gateway_host: str = "127.0.0.1"
    llm_gateway_port: int = 4000
    litellm_master_key: str
    embedding_model: str = "iaf-embedding"
    extraction_model: str = "gemini-vertex-flash"
    answer_model: str = "gemini-vertex-flash"

    # Documents deposes par les creators (US3.1). Volume dedie dans
    # compose.yaml ; chemin local par defaut pour le dev hors Docker.
    documents_dir: Path = Path("./data/documents")

    # Fuseki (ontologies OWL, ADR 0001) : source de verite des concepts
    # semantiques induits (IAF-E7 US7.5). Memes conventions que Neo4j/Postgres.
    fuseki_host: str = "127.0.0.1"
    fuseki_port: int = 3030
    fuseki_dataset: str = "iaf"

    # Seuil du score COMBINE (structurel + semantique, IAF-E7 US7.4 decide le
    # 2026-09-28) pour rattacher un document a une classe existante plutot que
    # d'en creer une nouvelle (US7.5). Baisse de 0.90 a 0.50 le 2026-10-01
    # (demande explicite de l'utilisateur, suite a la mesure reelle IAF-125 :
    # un document reellement rattache a sa classe - structurel 1.0, semantique
    # 0.417 - combine a 0.709, reste sous 0.90 a cause de termes generiques
    # qui diluent le score semantique, voir EPIC-IAF-E7 US7.2). Reste a
    # calibrer reellement via le banc d'evaluation (US7.7, toujours non fait) -
    # ce chiffre est le seuil RUNTIME, pas la preuve que l'algorithme l'atteint.
    recognition_threshold: float = 0.50

    # EPIC-IAF-E17 (2026-10-02) : cycle de vie des classes INCONNUES. Tous ces
    # parametres sont NON calibres (US7.7) - voir
    # docs/epics/EPIC-IAF-E17-classes-inconnues-cycle-de-vie.md.
    # Nombre de documents integres au-dela duquel une classe provisoire devient
    # OFFICIELLE (et entre dans la reconnaissance, US7.4).
    official_class_min_documents: int = 3
    # Fusion de classes provisoires par DENSITE de similarite : seuil adaptatif
    # max(plancher, moyenne + k * ecart-type) des similarites semantiques entre
    # paires de classes provisoires ; `merge_density_min_pairs` paires au moins
    # pour estimer la densite (sinon le plancher seul).
    # 0.30 (et non 0.50 initialement) apres mesure reelle du 2026-10-02 : documents
    # d'un meme domaine = 36 a 51 % de similarite entre classes, domaines sans
    # rapport = 0 a 12 % ; un plancher a 50 % laissait 2 documents d'astronomie isoles.
    merge_similarity_floor: float = 0.30
    merge_density_k: float = 1.0
    merge_density_min_pairs: int = 3
    # Recherche de corpus connus : score semantique minimal pour amorcer le
    # normaliseur de concepts avec le vocabulaire du corpus officiel le plus proche.
    corpus_seed_min_similarity: float = 0.30
    # Reduction de l'ontologie : un concept INDUIT mentionne par moins de ce
    # nombre de documents est "rare" (concepts importes jamais mentionnes : proteges).
    ontology_reduction_min_support: int = 2

    # Observabilite (ajoute le 2026-09-29, demande explicite : "genere le
    # code, l'infra... mais ne l'active pas"). Vide par defaut = handler Loki
    # jamais installe (observability.py), comportement identique a avant.
    # Le service docker-compose "loki"/"grafana" est lui-meme derriere le
    # profil "observability" (`docker compose --profile observability up`),
    # jamais demarre par defaut.
    loki_url: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def neo4j_uri(self) -> str:
        return f"bolt://{self.neo4j_host}:{self.neo4j_port}"

    @property
    def llm_gateway_url(self) -> str:
        return f"http://{self.llm_gateway_host}:{self.llm_gateway_port}"

    @property
    def fuseki_url(self) -> str:
        return f"http://{self.fuseki_host}:{self.fuseki_port}"


settings = Settings()
