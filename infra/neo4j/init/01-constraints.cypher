// Prerequis documente de neosemantics (n10s) : unicite de l'URI des ressources.
// Idempotent : rejoue sans erreur. n10s.graphconfig.init() est appele plus tard
// par la tache d'import d'ontologie (le mode de graphe depend du modele retenu).
CREATE CONSTRAINT n10s_unique_uri IF NOT EXISTS
FOR (r:Resource) REQUIRE r.uri IS UNIQUE;
