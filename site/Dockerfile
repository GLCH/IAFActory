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
    && chown -R site:site /app \
    && mkdir -p /data/documents && chown -R site:site /data
# /data/documents cree ici (proprietaire site) pour que le volume nomme
# site_documents (compose.yaml) herite de ces droits a son premier montage -
# sinon Docker le cree en root:root et l'utilisateur non-root ne peut pas y ecrire.

USER site
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
