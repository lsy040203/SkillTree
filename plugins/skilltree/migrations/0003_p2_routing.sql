CREATE TABLE run_contexts (
  run_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
  snapshot_json TEXT NOT NULL, trace_capture_enabled INTEGER NOT NULL,
  memory_read_enabled INTEGER NOT NULL, memory_write_enabled INTEGER NOT NULL,
  replay_capture_enabled INTEGER NOT NULL, created_at TEXT NOT NULL, retention_until TEXT NOT NULL
);
CREATE INDEX idx_run_contexts_retention ON run_contexts(retention_until);
CREATE TABLE route_offers (
  route_token_hash TEXT PRIMARY KEY CHECK(length(route_token_hash) = 71),
  workspace_id TEXT NOT NULL, session_id_hash TEXT NOT NULL,
  provisional_run_id TEXT UNIQUE REFERENCES run_contexts(run_id) ON DELETE SET NULL,
  trusted_snapshot_json TEXT NOT NULL CHECK(length(trusted_snapshot_json) BETWEEN 2 AND 131072 AND json_valid(trusted_snapshot_json) AND json_type(trusted_snapshot_json) = 'array'),
  candidate_json TEXT NOT NULL CHECK(length(candidate_json) BETWEEN 2 AND 16384 AND json_valid(candidate_json) AND json_type(candidate_json) = 'array'),
  candidate_snapshot_hash TEXT NOT NULL CHECK(length(candidate_snapshot_hash) = 71),
  prepared_at TEXT NOT NULL, expires_at TEXT NOT NULL, retention_until TEXT NOT NULL
);
CREATE INDEX idx_route_offers_expiry ON route_offers(expires_at);
CREATE TABLE route_decisions (
  run_id TEXT PRIMARY KEY REFERENCES run_contexts(run_id) ON DELETE CASCADE,
  route_token_hash TEXT NOT NULL UNIQUE CHECK(length(route_token_hash) = 71),
  candidate_snapshot_hash TEXT NOT NULL CHECK(length(candidate_snapshot_hash) = 71),
  decision_json TEXT NOT NULL CHECK(length(decision_json) BETWEEN 2 AND 4096 AND json_valid(decision_json) AND json_type(decision_json) = 'object'),
  committed_at TEXT NOT NULL, retention_until TEXT NOT NULL
);
