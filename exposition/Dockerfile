# Service d'exposition (ADR 0009) : agent de lecture des connaissances, serveur MCP (Streamable HTTP).
# Ne joint que Neo4j et la passerelle LLM : aucun acces a Postgres ni a Fuseki.
FROM python:3.13-slim

RUN useradd --system --create-home --shell /usr/sbin/nologin exposition

WORKDIR /app
COPY pyproject.toml ./
COPY exposition_service ./exposition_service

RUN pip install --no-cache-dir . && chown -R exposition:exposition /app
USER exposition

EXPOSE 8000
CMD ["python", "-m", "exposition_service.server"]
