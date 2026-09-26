# Apache Jena Fuseki : pas d'image officielle sur Docker Hub, on installe la
# release Apache (Jena 6 requiert Java 21 ou plus). Verification sha512 obligatoire.
FROM eclipse-temurin:21-jre

ARG JENA_VERSION=6.2.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    base="https://archive.apache.org/dist/jena/binaries"; \
    f="apache-jena-fuseki-${JENA_VERSION}.tar.gz"; \
    curl -fsSL -o "/tmp/$f" "$base/$f"; \
    curl -fsSL -o "/tmp/$f.sha512" "$base/$f.sha512"; \
    cd /tmp && sha512sum -c "$f.sha512"; \
    mkdir -p /opt/fuseki && tar -xzf "/tmp/$f" -C /opt/fuseki --strip-components=1; \
    rm -f /tmp/apache-jena-fuseki-*; \
    useradd --system --home-dir /fuseki --shell /usr/sbin/nologin fuseki; \
    mkdir -p /fuseki/databases /fuseki/run; \
    chown -R fuseki:fuseki /fuseki

ENV FUSEKI_HOME=/opt/fuseki \
    FUSEKI_BASE=/fuseki/run

COPY config.ttl /fuseki/config.ttl
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && chown fuseki:fuseki /fuseki/config.ttl

USER fuseki
WORKDIR /fuseki
EXPOSE 3030
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
