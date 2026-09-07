-- fix_postgres.sql
-- DarkSentinel PostgreSQL schema verifier/migration
-- Safe to run multiple times.

-- base tables (safe no-op if already present)
CREATE TABLE IF NOT EXISTS threat_results (
    id                   SERIAL PRIMARY KEY,
    source_hash          TEXT,
    onion_url            TEXT,
    engine               TEXT,
    query                TEXT,
    title                TEXT,
    clean_text           TEXT,
    category             TEXT,
    severity             TEXT,
    r_score              FLOAT,
    obfus_threat_score   FLOAT DEFAULT 0,
    obfuscation_detected BOOLEAN DEFAULT FALSE,
    guard_passed         BOOLEAN DEFAULT TRUE,
    scraped_at           TEXT,
    analyzed_at          TIMESTAMP DEFAULT NOW(),
    llm_mitre            TEXT,
    llm_mitre_tactic     TEXT,
    llm_mitre_tactic_id  TEXT,
    llm_mitre_technique_name TEXT,
    reasoning            TEXT,
    action               TEXT
);

-- threat_results core + AI columns
ALTER TABLE threat_results ADD COLUMN IF NOT EXISTS llm_mitre TEXT;
ALTER TABLE threat_results ADD COLUMN IF NOT EXISTS llm_mitre_tactic TEXT;
ALTER TABLE threat_results ADD COLUMN IF NOT EXISTS llm_mitre_tactic_id TEXT;
ALTER TABLE threat_results ADD COLUMN IF NOT EXISTS llm_mitre_technique_name TEXT;
ALTER TABLE threat_results ADD COLUMN IF NOT EXISTS reasoning TEXT;
ALTER TABLE threat_results ADD COLUMN IF NOT EXISTS action TEXT;
ALTER TABLE threat_results ADD COLUMN IF NOT EXISTS clean_text TEXT;
ALTER TABLE threat_results ADD COLUMN IF NOT EXISTS scraped_at TEXT;
ALTER TABLE threat_results ADD COLUMN IF NOT EXISTS obfus_threat_score FLOAT DEFAULT 0;
ALTER TABLE threat_results ADD COLUMN IF NOT EXISTS obfuscation_detected BOOLEAN DEFAULT FALSE;
ALTER TABLE threat_results ADD COLUMN IF NOT EXISTS guard_passed BOOLEAN DEFAULT TRUE;

-- analyst_queue: keep JSON + materialized convenience fields
CREATE TABLE IF NOT EXISTS analyst_queue (
    id         SERIAL PRIMARY KEY,
    alert_data JSONB,
    severity   TEXT,
    r_score    FLOAT,
    llm_mitre  TEXT,
    reasoning  TEXT,
    action     TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    reviewed   BOOLEAN DEFAULT FALSE
);

ALTER TABLE analyst_queue ADD COLUMN IF NOT EXISTS llm_mitre TEXT;
ALTER TABLE analyst_queue ADD COLUMN IF NOT EXISTS reasoning TEXT;
ALTER TABLE analyst_queue ADD COLUMN IF NOT EXISTS action TEXT;

-- quick schema check
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_name IN ('threat_results', 'analyst_queue')
ORDER BY table_name, ordinal_position;

-- quick counts
SELECT 'threat_results' AS table_name, COUNT(*) AS row_count FROM threat_results
UNION ALL
SELECT 'analyst_queue' AS table_name, COUNT(*) AS row_count FROM analyst_queue;
