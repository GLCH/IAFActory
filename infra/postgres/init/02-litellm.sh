#!/bin/bash
# Base et role dedies a la passerelle LLM : elle ne voit ni la base applicative
# ni les autres roles. Execute une seule fois, a la creation du volume Postgres :
# un volume existant ne rejoue pas ce script (creer alors le role a la main).
: "${LITELLM_DB_PASSWORD:?LITELLM_DB_PASSWORD requis}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE litellm LOGIN PASSWORD '${LITELLM_DB_PASSWORD}';
CREATE DATABASE litellm OWNER litellm;
REVOKE ALL ON DATABASE litellm FROM PUBLIC;
SQL
