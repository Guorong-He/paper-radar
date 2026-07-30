CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    doi TEXT,
    title TEXT NOT NULL,
    abstract TEXT NOT NULL,
    venue TEXT,
    authors_json TEXT NOT NULL,
    published_at TEXT NOT NULL,
    url TEXT,
    pdf_url TEXT,
    robot_type_tags_json TEXT NOT NULL,
    paper_type TEXT NOT NULL,
    signal_groups_json TEXT NOT NULL DEFAULT '[]',
    fulltext TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_analyses (
    paper_id TEXT PRIMARY KEY,
    core_insight TEXT NOT NULL,
    problem_frame TEXT NOT NULL DEFAULT '',
    first_principles TEXT NOT NULL DEFAULT '',
    mechanism TEXT NOT NULL DEFAULT '',
    boundary_advanced TEXT NOT NULL DEFAULT '',
    old_problem TEXT NOT NULL,
    why_it_works TEXT NOT NULL,
    true_novelty TEXT NOT NULL,
    evidence_summary TEXT NOT NULL,
    email_summary TEXT NOT NULL,
    importance_reason TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    FOREIGN KEY (paper_id) REFERENCES papers(id)
);

CREATE TABLE IF NOT EXISTS paper_scores (
    paper_id TEXT PRIMARY KEY,
    venue_author_score REAL NOT NULL,
    relevance_score REAL NOT NULL,
    evidence_score REAL NOT NULL,
    freshness_score REAL NOT NULL,
    diversity_score REAL NOT NULL,
    total_score REAL NOT NULL,
    scored_at TEXT NOT NULL,
    FOREIGN KEY (paper_id) REFERENCES papers(id)
);

CREATE TABLE IF NOT EXISTS digests (
    id TEXT PRIMARY KEY,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT,
    event_type TEXT NOT NULL,
    event_value REAL,
    occurred_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (paper_id) REFERENCES papers(id)
);
