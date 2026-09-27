# Site IAFActory (ADR 0007). Image simple : le paquet + son entrypoint qui
# applique les migrations avant de demarrer uvicorn.
FROM python:3.13-slim

RUN useradd --system --create-home --shell /usr/sbin/nologin site

WORKDIR /app
COPY pyproject.toml alembic.ini ./
COPY app ./app
COPY alembic ./alembic
COPY scripts ./scripts
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

RUN pip install --no-cache-dir . \
    && chmod +x /usr/local/bin/entrypoint.sh \
    && chown -R site:site /app

USER site
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
