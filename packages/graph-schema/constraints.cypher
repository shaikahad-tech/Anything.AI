// Neo4j constraint and index setup for Rabbit Hole Mapper.
// Run once on a fresh database. APOC and GDS must be installed.

// ─── Uniqueness constraints ──────────────────────────────────────────────────

CREATE CONSTRAINT topic_id_unique IF NOT EXISTS
FOR (n:Topic) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT source_id_unique IF NOT EXISTS
FOR (n:Source) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT claim_id_unique IF NOT EXISTS
FOR (n:Claim) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT person_id_unique IF NOT EXISTS
FOR (n:Person) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT org_id_unique IF NOT EXISTS
FOR (n:Organization) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT event_id_unique IF NOT EXISTS
FOR (n:Event) REQUIRE n.id IS UNIQUE;

// ─── Existence constraints ────────────────────────────────────────────────────
// Claims must always have a source_span_text (provenance invariant).
// Enforced here at the DB level in addition to the write-path code check.

// Note: property existence constraints require Neo4j Enterprise.
// Community edition relies on the write-path check in extraction.py.

// ─── Indexes ──────────────────────────────────────────────────────────────────

CREATE INDEX claim_rabbit_hole IF NOT EXISTS
FOR (n:Claim) ON (n.rabbit_hole_id);

CREATE INDEX source_rabbit_hole IF NOT EXISTS
FOR (n:Source) ON (n.rabbit_hole_id);

CREATE INDEX event_date IF NOT EXISTS
FOR (n:Event) ON (n.date_start);

CREATE INDEX source_url IF NOT EXISTS
FOR (n:Source) ON (n.url);

CREATE INDEX person_name IF NOT EXISTS
FOR (n:Person) ON (n.name);

CREATE INDEX org_name IF NOT EXISTS
FOR (n:Organization) ON (n.name);

// ─── Full-text search indexes ─────────────────────────────────────────────────

CREATE FULLTEXT INDEX claim_text_search IF NOT EXISTS
FOR (n:Claim) ON EACH [n.text];

CREATE FULLTEXT INDEX source_title_search IF NOT EXISTS
FOR (n:Source) ON EACH [n.title];

CREATE FULLTEXT INDEX entity_name_search IF NOT EXISTS
FOR (n:Person|Organization|Event) ON EACH [n.name];
