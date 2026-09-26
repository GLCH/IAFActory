# Passerelle LLM : LiteLLM proxy (image officielle, version epinglee) + jev-router
# (routeur experimental, MIT) recupere a un commit epingle et verifie.
# Voir docs/adr/0004-passerelle-llm.md.

# --- Etape 1 : recuperer jev-router a un commit exact -------------------------
FROM python:3.12-slim AS fetch
# Commit du 2026-09-16 ("feat: initial jev-router scaffold"), relu en entier
# avant epinglage (25 Ko). Changer cette valeur impose une nouvelle relecture.
ARG JEV_ROUTER_REF=583f0a1d1e0534cda3b6bbfa4b19aa1ec25d73a7
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    git init /src; cd /src; \
    git remote add origin https://github.com/prismhq/jev-router.git; \
    git fetch --depth 1 origin "$JEV_ROUTER_REF"; \
    git checkout --detach FETCH_HEAD; \
    test "$(git rev-parse HEAD)" = "$JEV_ROUTER_REF"

# --- Etape 2 : image finale ----------------------------------------------------
# Version conseillee par la doc LiteLLM (ne pas utiliser :latest) ; le tag existe
# (verifie par `docker manifest inspect` le 2026-09-26).
FROM docker.litellm.ai/berriai/litellm:v1.98.0

COPY --from=fetch /src/jev_router /opt/jev-router/jev_router
COPY --from=fetch /src/LICENSE /opt/jev-router/LICENSE
COPY litellm.yaml /etc/iaf/litellm.yaml
COPY router.yaml /etc/iaf/router.yaml

ENV PYTHONPATH=/opt/jev-router \
    JEV_ROUTER_CONFIG=/etc/iaf/router.yaml

# Le point d'entree de l'image de base est conserve ; la commande est fournie par
# compose.yaml (--config /etc/iaf/litellm.yaml --port 4000).
EXPOSE 4000
