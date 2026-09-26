// Modele du graphe de connaissances (docs/design/conception.md section 3).
// Uniquement des contraintes d'unicite, idempotentes. Les index vectoriels ne sont
// pas crees ici : leur dimension depend du modele d'embedding (IAF-E1 T7).
CREATE CONSTRAINT project_id IF NOT EXISTS FOR (n:Project) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT document_id IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:Chunk) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT document_class_id IF NOT EXISTS FOR (n:DocumentClass) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT glossary_id IF NOT EXISTS FOR (n:Glossary) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT ontology_id IF NOT EXISTS FOR (n:Ontology) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT domain_id IF NOT EXISTS FOR (n:Domain) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT term_id IF NOT EXISTS FOR (n:Term) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT struct_element_id IF NOT EXISTS FOR (n:StructElement) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT structural_profile_id IF NOT EXISTS FOR (n:StructuralProfile) REQUIRE n.id IS UNIQUE;
