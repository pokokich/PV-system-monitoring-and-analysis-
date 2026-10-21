-- AI analysis: runs of the owner's own language model over a summary of the data (web/analysis.py).
-- The settings live in app_setting (llm_provider, llm_model, llm_base_url, analysis_language);
-- the API key is never stored here (collector/secrets.py).
CREATE TABLE ai_analysis (
    id           INTEGER PRIMARY KEY,
    created_at   INTEGER NOT NULL,
    finished_at  INTEGER,
    status       TEXT NOT NULL CHECK (status IN ('running', 'done', 'error')),
    provider     TEXT,
    model        TEXT,
    language     TEXT,
    as_of        TEXT,
    question     TEXT,              -- an optional question from the owner
    result_json  TEXT,              -- the model's answer: overall, sections, levers
    error        TEXT,
    usage_json   TEXT
);
CREATE INDEX ai_analysis_created ON ai_analysis (created_at);

INSERT OR IGNORE INTO app_setting (key, value, updated_at) VALUES
    ('llm_provider', NULL, strftime('%s', 'now')),
    ('llm_model', NULL, strftime('%s', 'now')),
    ('llm_base_url', NULL, strftime('%s', 'now')),
    ('analysis_language', NULL, strftime('%s', 'now'));
