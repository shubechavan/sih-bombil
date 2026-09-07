-- schema_v2.sql
-- Dark Sentinel v2 — actor attribution schema
-- Re-runnable. Same style as v1's fix_postgres.sql.

-- ─── sources ────────────────────────────────────────────────────────────────
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

-- ─── actors: the resolved entity ────────────────────────────────────────────
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

-- ─── personas: one handle on one source ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS personas (
    id                SERIAL PRIMARY KEY,
    source_id         INTEGER REFERENCES sources(id) ON DELETE CASCADE,
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

CREATE INDEX IF NOT EXISTS idx_personas_norm   ON personas(handle_normalized);
CREATE INDEX IF NOT EXISTS idx_personas_actor  ON personas(actor_id);
CREATE INDEX IF NOT EXISTS idx_personas_source ON personas(source_id);

-- ─── identifiers ────────────────────────────────────────────────────────────
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

CREATE INDEX IF NOT EXISTS idx_identifiers_value ON identifiers(value_norm);
CREATE INDEX IF NOT EXISTS idx_identifiers_type  ON identifiers(type);

CREATE TABLE IF NOT EXISTS persona_identifiers (
    persona_id    INTEGER REFERENCES personas(id) ON DELETE CASCADE,
    identifier_id INTEGER REFERENCES identifiers(id) ON DELETE CASCADE,
    context       TEXT,                    -- snippet where it was found
    confidence    FLOAT DEFAULT 1.0,
    observed_at   TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (persona_id, identifier_id)
);

-- ─── posts: the stylometry corpus ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS posts (
    id          SERIAL PRIMARY KEY,
    persona_id  INTEGER REFERENCES personas(id) ON DELETE CASCADE,
    source_id   INTEGER REFERENCES sources(id) ON DELETE CASCADE,
    url         TEXT,
    title       TEXT,
    body        TEXT NOT NULL,
    body_hash   TEXT,                      -- sha256, dedupe
    category    TEXT,
    posted_at   TIMESTAMP,
    scraped_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_posts_persona ON posts(persona_id);
CREATE INDEX IF NOT EXISTS idx_posts_time    ON posts(posted_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_posts_hash ON posts(body_hash);

-- ─── writeprints: cached feature vectors ────────────────────────────────────
CREATE TABLE IF NOT EXISTS writeprints (
    persona_id   INTEGER PRIMARY KEY REFERENCES personas(id) ON DELETE CASCADE,
    char_count   INTEGER,
    vector       BYTEA,                    -- pickled/np.tobytes sparse vector
    features     JSONB,                    -- readable: func word freqs, punct ratios, emoji
    hour_hist    JSONB,                    -- 24 normalised buckets
    computed_at  TIMESTAMP DEFAULT NOW()
);

-- ─── links: persona to persona ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS links (
    id            SERIAL PRIMARY KEY,
    persona_a     INTEGER REFERENCES personas(id) ON DELETE CASCADE,
    persona_b     INTEGER REFERENCES personas(id) ON DELETE CASCADE,
    score         FLOAT NOT NULL,          -- attribution confidence A, 0..1
    band          TEXT NOT NULL,           -- CONFIRMED | PROBABLE | POSSIBLE | WEAK
    h_score       FLOAT DEFAULT 0,         -- hard identifier component
    s_score       FLOAT,                   -- stylometry (NULL if below text floor)
    b_score       FLOAT,                   -- behavioural
    i_score       FLOAT DEFAULT 0,         -- infrastructure
    evidence      JSONB NOT NULL,          -- [{type, detail, weight}]
    method        TEXT,                    -- which pass created it
    reviewed      BOOLEAN DEFAULT FALSE,
    analyst_note  TEXT,
    computed_at   TIMESTAMP DEFAULT NOW(),
    CHECK (persona_a < persona_b),         -- store each pair once
    UNIQUE (persona_a, persona_b)
);

CREATE INDEX IF NOT EXISTS idx_links_score ON links(score DESC);
CREATE INDEX IF NOT EXISTS idx_links_band  ON links(band);
CREATE INDEX IF NOT EXISTS idx_links_a     ON links(persona_a);
CREATE INDEX IF NOT EXISTS idx_links_b     ON links(persona_b);

-- ─── recon ──────────────────────────────────────────────────────────────────
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
    robots_txt       TEXT,
    clearnet_refs    JSONB,                -- absolute clearnet URLs found in the HTML
    headers          JSONB,
    misconfig_score  FLOAT DEFAULT 0,      -- how leaky this service is, 0..1
    scanned_at       TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_infra_onion   ON infra_findings(onion_url);
CREATE INDEX IF NOT EXISTS idx_infra_favicon ON infra_findings(favicon_hash);
CREATE INDEX IF NOT EXISTS idx_infra_serial  ON infra_findings(tls_serial);

CREATE TABLE IF NOT EXISTS infra_correlations (
    id             SERIAL PRIMARY KEY,
    finding_id     INTEGER REFERENCES infra_findings(id) ON DELETE CASCADE,
    onion_url      TEXT NOT NULL,
    clearnet_host  TEXT NOT NULL,
    clearnet_ip    TEXT,
    match_type     TEXT NOT NULL,          -- tls_serial | tls_san | favicon | etag | banner
    score          FLOAT NOT NULL,
    evidence       JSONB,
    provider       TEXT,                   -- shodan | censys | fixtures
    observed_at    TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_corr_onion ON infra_correlations(onion_url);
CREATE INDEX IF NOT EXISTS idx_corr_score ON infra_correlations(score DESC);

-- ─── audit ──────────────────────────────────────────────────────────────────
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

CREATE INDEX IF NOT EXISTS idx_scans_time ON scans(started_at DESC);

-- ─── convenience view for the actor table UI ────────────────────────────────
CREATE OR REPLACE VIEW v_actor_summary AS
SELECT
    a.id,
    a.label,
    a.category,
    a.first_seen,
    a.last_seen,
    a.max_confidence,
    COUNT(DISTINCT p.id)          AS persona_count,
    COUNT(DISTINCT p.source_id)   AS source_count,
    COUNT(DISTINCT pi.identifier_id) AS identifier_count,
    MAX(p.last_scan_at)           AS last_scan_at
FROM actors a
LEFT JOIN personas p            ON p.actor_id = a.id
LEFT JOIN persona_identifiers pi ON pi.persona_id = p.id
GROUP BY a.id;
