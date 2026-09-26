#!/bin/sh
# Genere shiro.ini au demarrage : l'admin Fuseki (/$/**) est ouvert hors
# localhost (necessaire derriere le port publie Docker) mais protege par mot de
# passe. /$/ping reste anonyme pour le healthcheck.
set -eu

: "${FUSEKI_ADMIN_PASSWORD:?FUSEKI_ADMIN_PASSWORD requis}"

mkdir -p "$FUSEKI_BASE"
cat > "$FUSEKI_BASE/shiro.ini" <<EOF
[users]
admin=${FUSEKI_ADMIN_PASSWORD},admins

[roles]
admins=*

[urls]
/\$/ping=anon
/\$/**=authcBasic,roles[admins]
/**=anon
EOF

exec "$FUSEKI_HOME/fuseki-server" --config /fuseki/config.ttl
