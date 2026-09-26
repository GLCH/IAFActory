-- Executee une seule fois a la creation du volume. Le schema metier
-- (utilisateurs, roles, agents, groupes) viendra avec les migrations de l'API.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
