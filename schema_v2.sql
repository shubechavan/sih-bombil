-- schema_v2.sql
-- Dark Sentinel v2 — actor attribution schema
--
-- Re-runnable. Same style as v1's legacy/fix_postgres.sql:
--   CREATE TABLE IF NOT EXISTS  +  ALTER TABLE ADD COLUMN IF NOT EXISTS
-- Apply with:  psql $PG_URL -f schema_v2.sql
--
-- CONVENTIONS
--   * All TIMESTAMP columns are naive and store UTC. We deliberately do NOT use
--     TIMESTAMPTZ: behavioural scoring builds posting-hour histograms, and with
--     TIMESTAMPTZ the result of EXTRACT(HOUR ...) depends on the session TimeZone
--     setting, which would make the histogram non-reproducible across clients.
--     db.py provides utcnow() and every writer must use it.
--   * schema_v2.sql is the single source of DDL. db.py mirrors it but must never
--     create it (no Base.metadata.create_all() in any normal code path).
--   * Section 2 (ALTER) mirrors every column in section 1 so that a database
--     created by an older revision of this file catches up on re-run.

SET client_encoding = 'UTF8';

-- ============================================================================
-- 1. TABLES
-- ============================================================================

-- --- sources: a site (onion or clearnet) -----------------------------------
CREATE TABLE IF NOT EXISTS sources (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    url           TEXT UNIQUE NOT NULL,
    kind          TEXT,                    -- market | forum | paste | deepweb
    is_onion      BOOLEAN DEFAULT TRUE,
    reliability   FLOAT DEFAULT 0.5,       -- source quality weight, 0..1
    first_seen    TIMESTAMP DEFAULT NOW(),
    last_scan_at  TIMESTAMP,
    active        BOOLEAN DEFAULT TRUE
);

-- --- actors: the resolved entity --------------------------------------------
CREATE TABLE IF NOT EXISTS actors (
    id             SERIAL PRIMARY KEY,
    label          TEXT,                   -- display name, usually the earliest handle
    category       TEXT,                   -- drugs | arms | data | hacking | laundering | other
    first_seen     TIMESTAMP,
    last_seen      TIMESTAMP,
    max_confidence FLOAT DEFAULT 0,        -- strongest link inside the cluster
    notes          TEXT,
    created_at     TIMESTAMP DEFAULT NOW(),
    updated_at     TIMESTAMP DEFAULT NOW()
);

-- --- personas: one handle on one source (the raw observation) ---------------
CREATE TABLE IF NOT EXISTS personas (
    id                SERIAL PRIMARY KEY,
    source_id         INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    actor_id          INTEGER REFERENCES actors(id) ON DELETE SET NULL,
    handle            TEXT NOT NULL,
    handle_normalized TEXT NOT NULL,       -- leet_decode + fold + strip separators
    profile_url       TEXT,
    bio               TEXT,
    category          TEXT,
    trust_score       FLOAT,               -- marketplace's own vendor rating if present
    post_count        INTEGER DEFAULT 0,
    first_seen        TIMESTAMP,
    last_seen         TIMESTAMP,
    last_scan_at      TIMESTAMP,
    UNIQUE (source_id, handle)
);

-- --- identifiers ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS identifiers (
    id          SERIAL PRIMARY KEY,
    type        TEXT NOT NULL,             -- pgp_fpr | btc | eth | xmr | ltc | email
                                           -- | jabber | session | telegram | onion_mirror
    value       TEXT NOT NULL,
    value_norm  TEXT,                      -- lowercased / checksum-stripped for matching
    validated   BOOLEAN DEFAULT FALSE,     -- passed checksum where applicable
    meta        JSONB,                     -- pgp: keyid, uid, created; wallet: chain
    first_seen  TIMESTAMP DEFAULT NOW(),
    last_seen   TIMESTAMP DEFAULT NOW(),
    UNIQUE (type, value)
);

CREATE TABLE IF NOT EXISTS persona_identifiers (
    persona_id    INTEGER NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    identifier_id INTEGER NOT NULL REFERENCES identifiers(id) ON DELETE CASCADE,
    context       TEXT,                    -- snippet where it was found
    confidence    FLOAT DEFAULT 1.0,
    observed_at   TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (persona_id, identifier_id)
);

-- --- posts: the stylometry corpus -------------------------------------------
CREATE TABLE IF NOT EXISTS posts (
    id          SERIAL PRIMARY KEY,
    persona_id  INTEGER NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    source_id   INTEGER REFERENCES sources(id) ON DELETE CASCADE,
    url         TEXT,
    title       TEXT,
    body        TEXT NOT NULL,
    body_hash   TEXT NOT NULL,             -- sha256 of body, dedupe key within a persona
    category    TEXT,
    posted_at   TIMESTAMP,
    scraped_at  TIMESTAMP DEFAULT NOW()
);

-- --- writeprints: cached feature vectors ------------------------------------
CREATE TABLE IF NOT EXISTS writeprints (
    persona_id      INTEGER PRIMARY KEY REFERENCES personas(id) ON DELETE CASCADE,
    char_count      INTEGER,
    vector          BYTEA,                 -- np.tobytes of the dense writeprint
    features        JSONB,                 -- readable: func word freqs, punct ratios, emoji
    hour_hist       JSONB,                 -- 24 normalised buckets
    feature_version TEXT,                  -- extractor version; a cached vector from a
                                           -- different version must not be compared
    computed_at     TIMESTAMP DEFAULT NOW()
);

-- --- links: persona to persona ----------------------------------------------
CREATE TABLE IF NOT EXISTS links (
    id            SERIAL PRIMARY KEY,
    persona_a     INTEGER NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    persona_b     INTEGER NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    score         FLOAT NOT NULL,          -- attribution confidence A, 0..1
    band          TEXT NOT NULL,           -- CONFIRMED | PROBABLE | POSSIBLE | WEAK
    h_score       FLOAT DEFAULT 0,         -- hard identifier component
    s_score       FLOAT,                   -- stylometry (NULL if below the 300-char floor)
    b_score       FLOAT,                   -- behavioural
    i_score       FLOAT DEFAULT 0,         -- infrastructure
    evidence      JSONB NOT NULL,          -- [{type, detail, weight}] — never empty
    method        TEXT,                    -- which pass created it
    reviewed      BOOLEAN DEFAULT FALSE,
    analyst_note  TEXT,
    computed_at   TIMESTAMP DEFAULT NOW(),
    CHECK (persona_a < persona_b),         -- store each pair once, lower id first
    UNIQUE (persona_a, persona_b)
);

-- --- recon ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS infra_findings (
    id               SERIAL PRIMARY KEY,
    source_id        INTEGER REFERENCES sources(id) ON DELETE CASCADE,
    onion_url        TEXT NOT NULL,
    server_banner    TEXT,
    powered_by       TEXT,
    etag             TEXT,
    favicon_hash     TEXT,                 -- mmh3, shodan-compatible
    status_exposed   BOOLEAN DEFAULT FALSE,-- /server-status or /server-info returned 200
    default_page     BOOLEAN DEFAULT FALSE,
    dir_listing      BOOLEAN DEFAULT FALSE,
    tls_subject      TEXT,
    tls_issuer       TEXT,
    tls_serial       TEXT,
    tls_sans         JSONB,
    tls_not_before   TIMESTAMP,
    robots_txt       TEXT,
    sitemap_xml      TEXT,
    html_comments    JSONB,                -- comments left in the served HTML
    generator_meta   TEXT,                 -- <meta name="generator">
    clearnet_refs    JSONB,                -- absolute clearnet URLs found in the HTML
    headers          JSONB,
    misconfig_score  FLOAT DEFAULT 0,      -- how leaky this service is, 0..1
    scanned_at       TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS infra_correlations (
    id             SERIAL PRIMARY KEY,
    finding_id     INTEGER REFERENCES infra_findings(id) ON DELETE CASCADE,
    onion_url      TEXT NOT NULL,
    clearnet_host  TEXT NOT NULL,
    clearnet_ip    TEXT,
    clearnet_port  INTEGER,
    match_type     TEXT NOT NULL,          -- tls_serial | tls_san | favicon | etag | banner
    score          FLOAT NOT NULL,
    evidence       JSONB,
    provider       TEXT,                   -- shodan | censys | fixtures
    observed_at    TIMESTAMP DEFAULT NOW()
);

-- --- audit ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scans (
    id              SERIAL PRIMARY KEY,
    operator_id     TEXT,
    mode            TEXT,                  -- manual | scheduled
    data_source     TEXT,                  -- fixtures | live
    query           TEXT,
    sources_touched JSONB,
    personas_new    INTEGER DEFAULT 0,
    links_new       INTEGER DEFAULT 0,
    action_hash     TEXT,                  -- sha256 of the action payload
    started_at      TIMESTAMP DEFAULT NOW(),
    finished_at     TIMESTAMP,
    status          TEXT DEFAULT 'running',
    error           TEXT
);

-- ============================================================================
-- 2. COLUMN MIGRATIONS
--    Mirrors section 1 so a database created by an older revision catches up.
--    No-ops on a fresh database.
-- ============================================================================

ALTER TABLE sources ADD COLUMN IF NOT EXISTS name         TEXT;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS url          TEXT;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS kind         TEXT;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS is_onion     BOOLEAN DEFAULT TRUE;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS reliability  FLOAT DEFAULT 0.5;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS first_seen   TIMESTAMP DEFAULT NOW();
ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_scan_at TIMESTAMP;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS active       BOOLEAN DEFAULT TRUE;

ALTER TABLE actors ADD COLUMN IF NOT EXISTS label          TEXT;
ALTER TABLE actors ADD COLUMN IF NOT EXISTS category       TEXT;
ALTER TABLE actors ADD COLUMN IF NOT EXISTS first_seen     TIMESTAMP;
ALTER TABLE actors ADD COLUMN IF NOT EXISTS last_seen      TIMESTAMP;
ALTER TABLE actors ADD COLUMN IF NOT EXISTS max_confidence FLOAT DEFAULT 0;
ALTER TABLE actors ADD COLUMN IF NOT EXISTS notes          TEXT;
ALTER TABLE actors ADD COLUMN IF NOT EXISTS created_at     TIMESTAMP DEFAULT NOW();
ALTER TABLE actors ADD COLUMN IF NOT EXISTS updated_at     TIMESTAMP DEFAULT NOW();

ALTER TABLE personas ADD COLUMN IF NOT EXISTS source_id         INTEGER REFERENCES sources(id) ON DELETE CASCADE;
ALTER TABLE personas ADD COLUMN IF NOT EXISTS actor_id          INTEGER REFERENCES actors(id) ON DELETE SET NULL;
ALTER TABLE personas ADD COLUMN IF NOT EXISTS handle            TEXT;
ALTER TABLE personas ADD COLUMN IF NOT EXISTS handle_normalized TEXT;
ALTER TABLE personas ADD COLUMN IF NOT EXISTS profile_url       TEXT;
ALTER TABLE personas ADD COLUMN IF NOT EXISTS bio               TEXT;
ALTER TABLE personas ADD COLUMN IF NOT EXISTS category          TEXT;
ALTER TABLE personas ADD COLUMN IF NOT EXISTS trust_score       FLOAT;
ALTER TABLE personas ADD COLUMN IF NOT EXISTS post_count        INTEGER DEFAULT 0;
ALTER TABLE personas ADD COLUMN IF NOT EXISTS first_seen        TIMESTAMP;
ALTER TABLE personas ADD COLUMN IF NOT EXISTS last_seen         TIMESTAMP;
ALTER TABLE personas ADD COLUMN IF NOT EXISTS last_scan_at      TIMESTAMP;

ALTER TABLE identifiers ADD COLUMN IF NOT EXISTS type       TEXT;
ALTER TABLE identifiers ADD COLUMN IF NOT EXISTS value      TEXT;
ALTER TABLE identifiers ADD COLUMN IF NOT EXISTS value_norm TEXT;
ALTER TABLE identifiers ADD COLUMN IF NOT EXISTS validated  BOOLEAN DEFAULT FALSE;
ALTER TABLE identifiers ADD COLUMN IF NOT EXISTS meta       JSONB;
ALTER TABLE identifiers ADD COLUMN IF NOT EXISTS first_seen TIMESTAMP DEFAULT NOW();
ALTER TABLE identifiers ADD COLUMN IF NOT EXISTS last_seen  TIMESTAMP DEFAULT NOW();

ALTER TABLE persona_identifiers ADD COLUMN IF NOT EXISTS context     TEXT;
ALTER TABLE persona_identifiers ADD COLUMN IF NOT EXISTS confidence  FLOAT DEFAULT 1.0;
ALTER TABLE persona_identifiers ADD COLUMN IF NOT EXISTS observed_at TIMESTAMP DEFAULT NOW();

ALTER TABLE posts ADD COLUMN IF NOT EXISTS persona_id INTEGER REFERENCES personas(id) ON DELETE CASCADE;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS source_id  INTEGER REFERENCES sources(id) ON DELETE CASCADE;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS url        TEXT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS title      TEXT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS body       TEXT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS body_hash  TEXT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS category   TEXT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS posted_at  TIMESTAMP;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS scraped_at TIMESTAMP DEFAULT NOW();

ALTER TABLE writeprints ADD COLUMN IF NOT EXISTS char_count      INTEGER;
ALTER TABLE writeprints ADD COLUMN IF NOT EXISTS vector          BYTEA;
ALTER TABLE writeprints ADD COLUMN IF NOT EXISTS features        JSONB;
ALTER TABLE writeprints ADD COLUMN IF NOT EXISTS hour_hist       JSONB;
ALTER TABLE writeprints ADD COLUMN IF NOT EXISTS feature_version TEXT;
ALTER TABLE writeprints ADD COLUMN IF NOT EXISTS computed_at     TIMESTAMP DEFAULT NOW();

ALTER TABLE links ADD COLUMN IF NOT EXISTS persona_a    INTEGER REFERENCES personas(id) ON DELETE CASCADE;
ALTER TABLE links ADD COLUMN IF NOT EXISTS persona_b    INTEGER REFERENCES personas(id) ON DELETE CASCADE;
ALTER TABLE links ADD COLUMN IF NOT EXISTS score        FLOAT;
ALTER TABLE links ADD COLUMN IF NOT EXISTS band         TEXT;
ALTER TABLE links ADD COLUMN IF NOT EXISTS h_score      FLOAT DEFAULT 0;
ALTER TABLE links ADD COLUMN IF NOT EXISTS s_score      FLOAT;
ALTER TABLE links ADD COLUMN IF NOT EXISTS b_score      FLOAT;
ALTER TABLE links ADD COLUMN IF NOT EXISTS i_score      FLOAT DEFAULT 0;
ALTER TABLE links ADD COLUMN IF NOT EXISTS evidence     JSONB;
ALTER TABLE links ADD COLUMN IF NOT EXISTS method       TEXT;
ALTER TABLE links ADD COLUMN IF NOT EXISTS reviewed     BOOLEAN DEFAULT FALSE;
ALTER TABLE links ADD COLUMN IF NOT EXISTS analyst_note TEXT;
ALTER TABLE links ADD COLUMN IF NOT EXISTS computed_at  TIMESTAMP DEFAULT NOW();

ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS source_id       INTEGER REFERENCES sources(id) ON DELETE CASCADE;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS onion_url       TEXT;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS server_banner   TEXT;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS powered_by      TEXT;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS etag            TEXT;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS favicon_hash    TEXT;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS status_exposed  BOOLEAN DEFAULT FALSE;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS default_page    BOOLEAN DEFAULT FALSE;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS dir_listing     BOOLEAN DEFAULT FALSE;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS tls_subject     TEXT;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS tls_issuer      TEXT;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS tls_serial      TEXT;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS tls_sans        JSONB;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS tls_not_before  TIMESTAMP;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS robots_txt      TEXT;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS sitemap_xml     TEXT;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS html_comments   JSONB;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS generator_meta  TEXT;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS clearnet_refs   JSONB;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS headers         JSONB;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS misconfig_score FLOAT DEFAULT 0;
ALTER TABLE infra_findings ADD COLUMN IF NOT EXISTS scanned_at      TIMESTAMP DEFAULT NOW();

ALTER TABLE infra_correlations ADD COLUMN IF NOT EXISTS finding_id    INTEGER REFERENCES infra_findings(id) ON DELETE CASCADE;
ALTER TABLE infra_correlations ADD COLUMN IF NOT EXISTS onion_url     TEXT;
ALTER TABLE infra_correlations ADD COLUMN IF NOT EXISTS clearnet_host TEXT;
ALTER TABLE infra_correlations ADD COLUMN IF NOT EXISTS clearnet_ip   TEXT;
ALTER TABLE infra_correlations ADD COLUMN IF NOT EXISTS clearnet_port INTEGER;
ALTER TABLE infra_correlations ADD COLUMN IF NOT EXISTS match_type    TEXT;
ALTER TABLE infra_correlations ADD COLUMN IF NOT EXISTS score         FLOAT;
ALTER TABLE infra_correlations ADD COLUMN IF NOT EXISTS evidence      JSONB;
ALTER TABLE infra_correlations ADD COLUMN IF NOT EXISTS provider      TEXT;
ALTER TABLE infra_correlations ADD COLUMN IF NOT EXISTS observed_at   TIMESTAMP DEFAULT NOW();

ALTER TABLE scans ADD COLUMN IF NOT EXISTS operator_id     TEXT;
ALTER TABLE scans ADD COLUMN IF NOT EXISTS mode            TEXT;
ALTER TABLE scans ADD COLUMN IF NOT EXISTS data_source     TEXT;
ALTER TABLE scans ADD COLUMN IF NOT EXISTS query           TEXT;
ALTER TABLE scans ADD COLUMN IF NOT EXISTS sources_touched JSONB;
ALTER TABLE scans ADD COLUMN IF NOT EXISTS personas_new    INTEGER DEFAULT 0;
ALTER TABLE scans ADD COLUMN IF NOT EXISTS links_new       INTEGER DEFAULT 0;
ALTER TABLE scans ADD COLUMN IF NOT EXISTS action_hash     TEXT;
ALTER TABLE scans ADD COLUMN IF NOT EXISTS started_at      TIMESTAMP DEFAULT NOW();
ALTER TABLE scans ADD COLUMN IF NOT EXISTS finished_at     TIMESTAMP;
ALTER TABLE scans ADD COLUMN IF NOT EXISTS status          TEXT DEFAULT 'running';
ALTER TABLE scans ADD COLUMN IF NOT EXISTS error           TEXT;

-- Columns that must never be NULL. Applied here rather than only in section 1
-- so an older database gains them too. Skipped with a notice if existing rows
-- would violate, so the script stays re-runnable against dirty data.
DO $$
DECLARE
    t   TEXT;
    c   TEXT;
    n   BIGINT;
    pairs TEXT[][] := ARRAY[
        ['personas', 'source_id'],
        ['personas', 'handle'],
        ['personas', 'handle_normalized'],
        ['posts',    'persona_id'],
        ['posts',    'body'],
        ['posts',    'body_hash'],
        ['links',    'persona_a'],
        ['links',    'persona_b'],
        ['links',    'score'],
        ['links',    'band'],
        ['links',    'evidence'],
        ['persona_identifiers', 'persona_id'],
        ['persona_identifiers', 'identifier_id'],
        ['identifiers', 'type'],
        ['identifiers', 'value'],
        ['sources',  'name'],
        ['sources',  'url']
    ];
BEGIN
    FOR i IN 1 .. array_length(pairs, 1) LOOP
        t := pairs[i][1];
        c := pairs[i][2];
        EXECUTE format('SELECT count(*) FROM %I WHERE %I IS NULL', t, c) INTO n;
        IF n = 0 THEN
            EXECUTE format('ALTER TABLE %I ALTER COLUMN %I SET NOT NULL', t, c);
        ELSE
            RAISE NOTICE 'skipped NOT NULL on %.%: % existing NULL row(s)', t, c, n;
        END IF;
    END LOOP;
END $$;

-- ============================================================================
-- 3. VALUE CONSTRAINTS
--    A typo'd identifier type ('bitcoin' instead of 'btc') silently breaks
--    hard-identifier scoring; an out-of-range score corrupts banding. Cheap to
--    catch at the boundary. Added idempotently.
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'ck_identifiers_type'
                     AND conrelid = 'identifiers'::regclass) THEN
        ALTER TABLE identifiers ADD CONSTRAINT ck_identifiers_type CHECK (
            type IN ('pgp_fpr', 'btc', 'eth', 'xmr', 'ltc', 'email',
                     'jabber', 'session', 'telegram', 'onion_mirror')
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'ck_links_band'
                     AND conrelid = 'links'::regclass) THEN
        ALTER TABLE links ADD CONSTRAINT ck_links_band CHECK (
            band IN ('CONFIRMED', 'PROBABLE', 'POSSIBLE', 'WEAK')
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'ck_links_score'
                     AND conrelid = 'links'::regclass) THEN
        ALTER TABLE links ADD CONSTRAINT ck_links_score CHECK (
            score >= 0 AND score <= 1
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'ck_infra_corr_score'
                     AND conrelid = 'infra_correlations'::regclass) THEN
        ALTER TABLE infra_correlations ADD CONSTRAINT ck_infra_corr_score CHECK (
            score >= 0 AND score <= 1
        );
    END IF;
END $$;

-- ============================================================================
-- 4. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_personas_norm   ON personas(handle_normalized);
CREATE INDEX IF NOT EXISTS idx_personas_actor  ON personas(actor_id);
CREATE INDEX IF NOT EXISTS idx_personas_source ON personas(source_id);

-- idx_identifiers_value used to be built on value_norm despite its name, and
-- nothing indexed the raw value on its own — UNIQUE (type, value) only helps
-- when the type is also supplied.
DROP INDEX IF EXISTS idx_identifiers_value;
CREATE INDEX IF NOT EXISTS idx_identifiers_value_norm ON identifiers(value_norm);
CREATE INDEX IF NOT EXISTS idx_identifiers_value      ON identifiers(value);
CREATE INDEX IF NOT EXISTS idx_identifiers_type       ON identifiers(type);

-- "which personas share this identifier" is the core query of the hard-identifier
-- pass, and the composite primary key only indexes the persona_id prefix.
CREATE INDEX IF NOT EXISTS idx_pident_identifier ON persona_identifiers(identifier_id);

CREATE INDEX IF NOT EXISTS idx_posts_persona ON posts(persona_id);
CREATE INDEX IF NOT EXISTS idx_posts_time    ON posts(posted_at);

-- body_hash was globally UNIQUE, which meant two personas could never post the
-- same text: shared boilerplate and content mirrored across sources were dropped
-- on insert. Dedupe belongs within a persona.
DROP INDEX IF EXISTS idx_posts_hash;
CREATE UNIQUE INDEX IF NOT EXISTS idx_posts_persona_hash ON posts(persona_id, body_hash);

CREATE INDEX IF NOT EXISTS idx_links_score ON links(score DESC);
CREATE INDEX IF NOT EXISTS idx_links_band  ON links(band);
CREATE INDEX IF NOT EXISTS idx_links_a     ON links(persona_a);
CREATE INDEX IF NOT EXISTS idx_links_b     ON links(persona_b);

CREATE INDEX IF NOT EXISTS idx_infra_onion   ON infra_findings(onion_url);
CREATE INDEX IF NOT EXISTS idx_infra_favicon ON infra_findings(favicon_hash);
CREATE INDEX IF NOT EXISTS idx_infra_serial  ON infra_findings(tls_serial);

CREATE INDEX IF NOT EXISTS idx_corr_onion ON infra_correlations(onion_url);
CREATE INDEX IF NOT EXISTS idx_corr_score ON infra_correlations(score DESC);

CREATE INDEX IF NOT EXISTS idx_scans_time ON scans(started_at DESC);

-- ============================================================================
-- 5. VIEWS
--    DROP + CREATE rather than CREATE OR REPLACE: replacing a view fails if the
--    column list ever changes, which would break re-runnability on upgrade.
-- ============================================================================

DROP VIEW IF EXISTS v_actor_summary;
CREATE VIEW v_actor_summary AS
SELECT
    a.id,
    a.label,
    a.category,
    a.first_seen,
    a.last_seen,
    a.max_confidence,
    COUNT(DISTINCT p.id)             AS persona_count,
    COUNT(DISTINCT p.source_id)      AS source_count,
    COUNT(DISTINCT pi.identifier_id) AS identifier_count,
    MAX(p.last_scan_at)              AS last_scan_at
FROM actors a
LEFT JOIN personas p             ON p.actor_id = a.id
LEFT JOIN persona_identifiers pi ON pi.persona_id = p.id
GROUP BY a.id;

-- ============================================================================
-- 6. VERIFICATION
-- ============================================================================

SELECT table_name, COUNT(*) AS column_count
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('sources', 'actors', 'personas', 'identifiers',
                     'persona_identifiers', 'posts', 'writeprints', 'links',
                     'infra_findings', 'infra_correlations', 'scans')
GROUP BY table_name
ORDER BY table_name;

SELECT 'sources'             AS table_name, COUNT(*) AS row_count FROM sources
UNION ALL SELECT 'actors',              COUNT(*) FROM actors
UNION ALL SELECT 'personas',            COUNT(*) FROM personas
UNION ALL SELECT 'identifiers',         COUNT(*) FROM identifiers
UNION ALL SELECT 'persona_identifiers', COUNT(*) FROM persona_identifiers
UNION ALL SELECT 'posts',               COUNT(*) FROM posts
UNION ALL SELECT 'writeprints',         COUNT(*) FROM writeprints
UNION ALL SELECT 'links',               COUNT(*) FROM links
UNION ALL SELECT 'infra_findings',      COUNT(*) FROM infra_findings
UNION ALL SELECT 'infra_correlations',  COUNT(*) FROM infra_correlations
UNION ALL SELECT 'scans',               COUNT(*) FROM scans
ORDER BY table_name;
